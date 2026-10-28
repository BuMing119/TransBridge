from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from tests.ui.tools.ai_runtime_support import frozen_ai_request
from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter
from transbridge.ai_translator.shared_term_pool import SharedTermPool
from transbridge.ai_translator.shared_term_snapshot import FrozenTermEntry
from transbridge.ai_translator.task_reference_snapshot import TaskReferenceStore
from transbridge.ai_translator.term_database import DynamicTermDatabase, TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry, dump_terms_json
from transbridge.ai_translator.term_publication_conflict import TermPublicationConflict
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


@pytest.fixture
def publication(tmp_path):
    path = tmp_path / "shared.json"
    pool = SharedTermPool("project", "variant", "zh_CN", path=path)
    manager = SimpleNamespace(
        _shared_term_pool=pool,
        _generated_term_plugin_id="plugin.esp",
        get_dynamic_db=lambda: pool,
    )
    return SimpleNamespace(path=path, pool=pool, writer=GeneratedTermWriter(manager), manager=manager)


def test_write_guard_refuses_only_publication_and_keeps_one_diagnostic_per_term(publication):
    @contextmanager
    def changed_file():
        raise TermPublicationConflict("动态术语文件已被外部修改")
        yield  # pragma: no cover

    writer = GeneratedTermWriter(publication.manager, access=changed_file)
    observed = []
    for _ in range(2):
        assert writer.save([("Sword", "剑", "auto_name", "")], observer=lambda *_: observed.append(True)) == 0
    assert observed == []
    assert not publication.path.exists()
    assert publication.pool.revision == 0
    conflicts = publication.pool.conflicts_since(0)
    assert len(conflicts) == 1
    assert conflicts[0].plugin_id == "plugin.esp"
    assert conflicts[0].proposed_translation == "剑"


def test_standalone_writer_can_skip_external_write_conflicts_without_a_shared_pool(caplog):
    @contextmanager
    def changed_file():
        raise TermPublicationConflict("动态术语文件已被外部修改")
        yield  # pragma: no cover

    manager = SimpleNamespace()
    assert GeneratedTermWriter(manager, access=changed_file).save([("Sword", "剑", "auto", "")]) == 0
    assert "保留当前译文和任务参考" in caplog.text


@pytest.mark.parametrize("failure", [RuntimeError("invalid state"), OSError("disk failure")])
def test_unexpected_write_guard_failures_are_not_downgraded(publication, failure):
    @contextmanager
    def broken_guard():
        raise failure
        yield  # pragma: no cover

    writer = GeneratedTermWriter(publication.manager, access=broken_guard)
    with pytest.raises(type(failure), match=str(failure)):
        writer.save([("Sword", "剑", "auto", "")])
    assert publication.pool.conflicts_since(0) == ()


def _external_payload():
    return json.dumps({
        "identity": ["project", "variant", "zh_CN"],
        "revision": 1,
        "terms": [],
        "conflicts": [],
    }).encode()


def test_external_write_before_acknowledgement_rolls_back_only_task_publication(publication, monkeypatch):
    snapshot = publication.pool.snapshot()
    persist = publication.pool._write_bytes
    external = _external_payload()

    def external_after_save(payload):
        persist(payload)
        publication.path.write_bytes(external)

    monkeypatch.setattr(publication.pool, "_write_bytes", external_after_save)
    acknowledged = []
    assert publication.writer.save([("Sword", "剑", "auto", "")], observer=lambda *_: acknowledged.append(True)) == 0
    assert acknowledged == []
    assert publication.path.read_bytes() == external
    assert publication.pool.snapshot() is snapshot
    assert publication.pool.revision == 0
    assert publication.pool.conflicts_since(0)[0].term == "Sword"


def test_external_write_during_acknowledgement_preserves_external_bytes(publication):
    snapshot = publication.pool.snapshot()
    external = _external_payload()

    def external_after_acknowledgement(*_):
        publication.path.write_bytes(external)

    assert publication.writer.save([("Sword", "剑", "auto", "")], observer=external_after_acknowledgement) == 0
    assert publication.path.read_bytes() == external
    assert publication.pool.snapshot() is snapshot
    assert publication.pool.revision == 0


def test_corrupt_initial_shared_file_remains_an_error(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("invalid JSON", encoding="utf-8")
    with pytest.raises(ValueError, match="无法恢复共享术语库"):
        SharedTermPool("project", "variant", "zh_CN", path=path)


def test_another_tasks_active_write_only_skips_generated_term_publication(publication):
    external = SharedTermPool(*publication.pool.identity, path=publication.path)
    waiting, release = threading.Event(), threading.Event()

    def wait_before_publication(_terms):
        waiting.set()
        assert release.wait(5)

    with ThreadPoolExecutor(max_workers=1) as executor:
        active_write = executor.submit(
            external.publish, [("Shield", "盾", "external", "")], acknowledge=wait_before_publication
        )
        try:
            assert waiting.wait(3)
            assert publication.writer.save([("Sword", "剑", "auto", "")]) == 0
            assert publication.pool.snapshot().entries == ()
            assert publication.pool.conflicts_since(0)[0].reason == "external-write-conflict"
        finally:
            release.set()
        assert active_write.result(timeout=5) == [("Shield", "盾", "external", "")]
    assert {entry.term for entry in external.as_list()} == {"Shield"}


@pytest.fixture(params=[False, True], ids=["legacy-view", "frozen-task-view"])
def local_publication(tmp_path, monkeypatch, request):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda stem: str(tmp_path / stem)))
    (tmp_path / "local").mkdir()
    initial = TermEntry("Sword", "原参考", "auto", variants=["Blade"], metadata={"tags": ["original"]})
    seed = DynamicTermDatabase("local.esp")
    dump_terms_json(seed._path, [initial])
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    task_request = SimpleNamespace(run_id="local-write-race", config=config, spec=SimpleNamespace(mode="mixed"))
    task_request = frozen_ai_request(task_request)
    guard = TaskConsistency(task_request)
    store = TaskReferenceStore("local-write-race") if request.param else None

    def manager():
        result = TermDatabaseManager(config, "local.esp", reference_store=store, reference_source="source")
        result.load_all()
        return result

    current = manager()
    guard.observe_terms("source", "translation", current)
    database = current.get_dynamic_db()
    assert current.exact_match(["Sword", "External", "Task Name"]) == {"Sword": "原参考"}
    return SimpleNamespace(
        manager=current,
        new_manager=manager,
        store=store,
        database=database,
        path=Path(database._path),
        guard=guard,
        request=task_request,
        writer=GeneratedTermWriter(current, access=guard.terminology_write_access),
    )


def _assert_original_local_references(run, original, revision):
    database, manager = run.database, run.manager
    assert database.as_list() == [original]
    assert database.as_list()[0] is original
    assert original.translation == "原参考"
    assert original.variants == ["Blade"]
    assert original.metadata == {"tags": ["original"]}
    assert database.revision > revision
    assert manager.exact_match(["Sword", "External", "Task Name"]) == {"Sword": "原参考"}
    assert manager.match_terms_for_entry(SimpleNamespace(original="Sword External Task Name")) == {"Sword": "原参考"}
    assert manager.resolve_term("External") is None
    assert manager.resolve_term("Task Name") is None
    run.guard.require_current(run.request)


def test_local_reload_race_restores_existing_objects_and_excludes_external_additions(local_publication, monkeypatch):
    run = local_publication
    original = run.database.as_list()[0]
    captured = FrozenTermEntry.capture(original)
    revision = run.database.revision
    load = run.database.load
    external_bytes = []

    def change_then_load():
        dump_terms_json(
            run.path,
            [
                TermEntry("Sword", "外部改名", "manual", variants=["New Blade"], metadata={"tags": ["external"]}),
                TermEntry("External", "外部新增", "manual"),
            ],
        )
        external_bytes.append(run.path.read_bytes())
        load()
        # An uncommitted view cached inside a callback must also be invalidated.
        assert run.manager.exact_match(["External"]) == {"External": "外部新增"}

    monkeypatch.setattr(run.database, "load", change_then_load)
    assert run.writer.save([("Task Name", "任务新增", "auto", "")]) == 0
    assert run.path.read_bytes() == external_bytes[0]
    assert FrozenTermEntry.capture(original) == captured
    _assert_original_local_references(run, original, revision)
    if run.store is not None:
        retry = run.new_manager()
        assert retry.get_dynamic_db() is run.database
        assert retry.exact_match(["Sword", "External", "Task Name"]) == {"Sword": "原参考"}


def test_local_save_failure_restores_reference_records_without_touching_disk(local_publication, monkeypatch):
    run = local_publication
    original = run.database.as_list()[0]
    revision = run.database.revision
    before = run.path.read_bytes()

    def failed_save():
        assert run.manager.exact_match(["Task Name"]) == {"Task Name": "任务新增"}
        raise OSError("storage unavailable")

    monkeypatch.setattr(run.database, "save", failed_save)
    with pytest.raises(OSError, match="storage unavailable"):
        run.writer.save([("Sword", "任务改名", "auto", ""), ("Task Name", "任务新增", "auto", "")])
    assert run.path.read_bytes() == before
    _assert_original_local_references(run, original, revision)


@pytest.mark.parametrize("external_write", [False, True])
def test_local_acknowledgement_failure_restores_only_task_memory(local_publication, external_write):
    run = local_publication
    original = run.database.as_list()[0]
    revision = run.database.revision
    bytes_before_rejection = []

    def reject(current, terms):
        if external_write:
            dump_terms_json(run.path, [TermEntry("External", "外部最终内容", "manual")])
        bytes_before_rejection.append(run.path.read_bytes())
        if external_write:
            run.guard.record_generated_terms("source", current, terms)
        raise RuntimeError("acknowledgement rejected")

    terms = [("Sword", "任务改名", "auto", ""), ("Task Name", "任务新增", "auto", "")]
    if external_write:
        assert run.writer.save(terms, observer=reject) == 0
    else:
        with pytest.raises(RuntimeError, match="acknowledgement rejected"):
            run.writer.save(terms, observer=reject)
    assert run.path.read_bytes() == bytes_before_rejection[0]
    _assert_original_local_references(run, original, revision)
