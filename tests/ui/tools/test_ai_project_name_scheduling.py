from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from types import SimpleNamespace

from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.config.llm import EmbeddingConfig, LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import task_name_preparation
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


def _executor():
    pause = Event()
    pause.set()
    logs, progress = [], []
    return SimpleNamespace(
        shared_term_pool=SimpleNamespace(load=lambda: None),
        config=SimpleNamespace(max_concurrent=1),
        request=SimpleNamespace(spec=SimpleNamespace(overwrite=False)),
        stop=Event(),
        pause=pause,
        consistency=None,
        log=lambda *args: logs.append(args),
        progress=lambda *args: progress.append(args),
        logs=logs,
        events=progress,
    )


def _task(index):
    key = str(index)
    entry = TranslationEntry(key, key, f"Name {index}", "", 0, "WEAP:FULL")
    return SourceTask(key, key, f"{key}.esp", TranslationEntryCollection([entry]), (entry,), ())


def test_name_dispatch_is_bounded_and_stop_settles_unstarted_groups(monkeypatch):
    executor = _executor()
    tasks = tuple(_task(index) for index in range(8))
    entered, release = Event(), Event()
    lock = Lock()
    started, submitted = [], []

    class BoundedPool(ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            submitted.append(args[1].key)
            assert len(submitted) <= 2, "name sources must not queue beyond active capacity"
            return super().submit(fn, *args, **kwargs)

    manager = SimpleNamespace(
        lookup_context_for_entry=lambda _: None,
        _effective_terms=lambda _: [],
        _get_term_matcher_map=lambda _: {},
        _effective_loader=None,
    )
    monkeypatch.setattr(task_name_preparation, "_manager", lambda *_: manager)
    monkeypatch.setattr(task_name_preparation, "ThreadPoolExecutor", BoundedPool)

    def prepare(executor, task, selected):
        with lock:
            started.append(task.key)
            if len(started) == 2:
                entered.set()
        assert release.wait(3)
        return {index: ("", "任务已取消") for index, _ in selected}

    monkeypatch.setattr(task_name_preparation, "_prepare_source", prepare)
    with ThreadPoolExecutor(max_workers=1) as coordinator:
        future = coordinator.submit(task_name_preparation.prepare_project_names, executor, tasks)
        try:
            assert entered.wait(3)
        finally:
            executor.stop.set()
            release.set()
        cache = future.result(timeout=3)
    assert len(submitted) == len(started) == 2
    assert set(cache) == {task.key for task in tasks}
    assert all(item.reason == "任务已取消" for prepared in cache.values() for item in prepared.values())
    assert all("名称准备完成" not in message and "名称预翻译失败" not in message for _, message in executor.logs)
    assert all(event[0] in cache and event[1] == "project_names" for event in executor.events)
    assert executor.prepared_name_term_counts == {}


def test_cancellation_during_initial_scan_returns_without_loading_or_dispatching(monkeypatch):
    executor = _executor()
    task = _task(0)
    scanned = []

    class Entries:
        def __iter__(self):
            for index in range(100):
                scanned.append(index)
                if index == 2:
                    executor.stop.set()
                yield task.translate_entries[0]

    def unexpected(*_):
        raise AssertionError("a cancelled scan must not load a manager or submit a request")

    monkeypatch.setattr(task_name_preparation, "_manager", unexpected)
    source = SimpleNamespace(key="A", translate_entries=Entries())
    assert task_name_preparation.prepare_project_names(executor, (source,)) == {}
    assert scanned == [0, 1, 2]
    assert executor.prepared_name_term_counts == {}


def test_name_scan_never_initializes_semantic_vectors_or_mutates_run_config(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _: str(tmp_path / "source")))
    executor = _executor()
    executor.config = LLMConfig(
        term_priority=["dynamic"], enable_semantic_match=True, embedding=EmbeddingConfig(mode="api")
    )
    executor.shared_term_pool = SharedTermPool(
        "project", "variant", executor.config.target_lang, path=tmp_path / "pool.json"
    )
    executor.client = None
    executor.project_id = None
    executor.request.terminology_binding = SimpleNamespace(term_database_kwargs=lambda: {})

    def unexpected(*_):
        raise AssertionError("name planning must only read lexical terminology")

    monkeypatch.setattr(TermDatabaseManager, "_init_vector_index", unexpected)
    manager = task_name_preparation._manager(executor, _task(0))
    assert manager._load_completed
    assert manager._vector_index is None
    assert executor.config.enable_semantic_match is True
    assert executor.config.embedding.mode == "api"


def test_statistics_precede_term_loading_and_name_acceptance_does_not_rematch_reference_terms(monkeypatch):
    executor = _executor()
    tasks = (_task(0), _task(1))
    matched = []
    from transbridge.ai_translator.name_preparation_plan import NameAuthority

    class Authority:
        def __init__(self, manager, *_args, **_kwargs):
            self.source = manager

        def for_entry(self, entry, *, check_required_terms=True):
            matched.append((self.source, check_required_terms))
            return NameAuthority()

    def manager(_executor, task):
        for source in tasks:
            assert any(key == source.key and "统计完成" in message for key, message in executor.logs)
            assert any(event[0] == source.key and event[4].startswith("统计名称") for event in executor.events)
        assert any(key == task.key and "加载名称" in message for key, message in executor.logs)
        return task.key

    monkeypatch.setattr(task_name_preparation, "_manager", manager)
    monkeypatch.setattr(task_name_preparation, "SourceNameAuthority", Authority)
    groups, _managers, authorities = task_name_preparation._plan_names(executor, tasks)
    assert len(groups) == 2
    assert matched == [("0", False), ("1", False)]
    results = {index: ("译文", "") for index in range(len(groups))}
    task_name_preparation._validate_results(executor, groups, results, authorities)
    assert matched == [("0", False), ("1", False)]
    assert results == {index: ("译文", "") for index in range(len(groups))}


def test_no_names_does_not_load_terminology(monkeypatch):
    executor = _executor()
    task = _task(0)
    task.translate_entries[0].context = "BOOK:DESC"

    def unexpected(*_):
        raise AssertionError("a task without names must not open the name term database")

    executor.shared_term_pool.load = unexpected
    monkeypatch.setattr(task_name_preparation, "_manager", unexpected)
    assert task_name_preparation.prepare_project_names(executor, (task,)) == {}
