from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.generated_term_writer import GeneratedTermWriter
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


@pytest.fixture
def term_task(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda stem: str(tmp_path / stem)))
    config = LLMConfig(term_priority=["dynamic"], enable_semantic_match=False)
    request = SimpleNamespace(run_id="term-transactions", spec=SimpleNamespace(mode="mixed"), config=config)
    guard = TaskConsistency(request)

    def load(source, esp_path="shared.esp", stage="translation"):
        with guard.terminology_access():
            manager = TermDatabaseManager(config, esp_path)
            manager.load_all()
            guard.observe_terms(source, stage, manager)
        return manager

    def save(source, manager, terms, observer=None):
        def record(current, written):
            if observer is not None:
                observer(current, written)
            guard.record_generated_terms(source, current, written)

        return GeneratedTermWriter(manager, access=guard.terminology_write_access).save(terms, observer=record)

    return SimpleNamespace(config=config, request=request, guard=guard, load=load, save=save)


def test_polish_cannot_load_between_dynamic_save_and_ownership_registration(term_task):
    manager = term_task.load("source")
    persisted, release_registration, reading_attempted, reading_started = (threading.Event() for _ in range(4))
    terms = [("New Name", "新名字", "auto_name", "NPC_:FULL")]

    def before_registration(current, _terms):
        assert Path(current.get_dynamic_db()._path).is_file()
        persisted.set()
        assert release_registration.wait(5), "test did not release ownership registration"

    def read_polish():
        reading_attempted.set()
        with term_task.guard.terminology_access():
            reading_started.set()
            return term_task.load("source", stage="polish")

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(term_task.save, "source", manager, terms, before_registration)
        reading = None
        try:
            assert persisted.wait(3), "dynamic terms were not persisted"
            reading = pool.submit(read_polish)
            assert reading_attempted.wait(3)
            assert not reading_started.wait(0.15), "polish entered an unregistered terminology write"
        finally:
            release_registration.set()
        writing.result(timeout=5)
        assert reading is not None
        polished = reading.result(timeout=5)

    assert reading_started.is_set()
    assert {entry.term: entry.translation for entry in polished._merged_terms} == {"New Name": "新名字"}
    term_task.guard.require_current(term_task.request)


def test_sources_sharing_dynamic_path_preserve_and_accept_each_others_terms(term_task):
    first = term_task.load("source-a", "first/shared.esp")
    second = term_task.load("source-b", "second/shared.esp")
    assert first.get_dynamic_db()._path == second.get_dynamic_db()._path

    term_task.save("source-a", first, [("First Name", "第一个名字", "existing_name", "NPC_:FULL")])
    term_task.save("source-b", second, [("Second Name", "第二个名字", "auto_dialogue", "")])

    expected = {"First Name": "第一个名字", "Second Name": "第二个名字"}
    for source in ("source-a", "source-b"):
        retried = term_task.load(source, stage="polish")
        assert {entry.term: entry.translation for entry in retried._merged_terms} == expected
    term_task.guard.require_current(term_task.request)


def test_loaded_reference_differences_do_not_authorize_or_perform_file_writes(term_task):
    first = term_task.load("source-a", "first.esp")
    other = term_task.load("source-b", "other.esp")
    term_task.save("source-a", first, [("New Name", "新名字", "auto_name", "NPC_:FULL")])

    # Reference rows are not an application gate or permission to write.
    other._merged_terms.append(TermEntry("New Name", "新名字", "auto_name", "NPC_:FULL"))
    with term_task.guard.terminology_access():
        term_task.guard.observe_terms("source-b", "polish", other)
    assert not Path(other.get_dynamic_db()._path).exists()
    term_task.guard.require_current(term_task.request)


def test_external_edit_is_rejected_before_internal_write_can_acknowledge_it(term_task):
    manager = term_task.load("source")
    external = TermDatabaseManager(term_task.config, "shared.esp")
    external.load_all()
    external.get_dynamic_db().add_many_and_save([("External Name", "外部名称", "manual", "")])
    path = Path(external.get_dynamic_db()._path)
    external_bytes = path.read_bytes()
    observed = []

    written = term_task.save(
        "source", manager, [("Task Name", "任务名称", "auto_name", "NPC_:FULL")], lambda *_: observed.append(True)
    )

    assert written == 0
    assert observed == []
    assert path.read_bytes() == external_bytes
    term_task.guard.require_current(term_task.request)


def test_failed_save_does_not_register_terms_and_successful_retry_remains_valid(term_task, monkeypatch):
    manager = term_task.load("source")
    database = manager.get_dynamic_db()
    terms = [("New Name", "新名字", "existing_text", "")]
    observed = []

    def fail_save():
        raise OSError("dynamic terminology storage unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(database, "save", fail_save)
        with pytest.raises(OSError, match="storage unavailable"):
            term_task.save("source", manager, terms, lambda *_: observed.append(True))

    assert observed == []
    assert not Path(database._path).exists()
    assert term_task.load("source", stage="polish")._merged_terms == []
    term_task.guard.require_current(term_task.request)

    term_task.save("source", manager, terms, lambda *_: observed.append(True))
    assert observed == [True]
    retried = term_task.load("source", stage="polish")
    assert {entry.term: entry.translation for entry in retried._merged_terms} == {"New Name": "新名字"}
    term_task.guard.require_current(term_task.request)


@pytest.mark.parametrize("origin", ["auto_name", "manual"])
@pytest.mark.parametrize("preferred_source", ["dynamic", "json"])
def test_updating_existing_dynamic_terms_refreshes_live_lookup_without_changing_priority(
    tmp_path, monkeypatch, origin, preferred_source
):
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda stem: str(tmp_path / stem)))
    json_path = tmp_path / "terms.json"
    json_path.write_text('[{"term": "Name", "translation": "文件译名"}]', encoding="utf-8")
    priority = ["dynamic", "json"] if preferred_source == "dynamic" else ["json", "dynamic"]
    config = LLMConfig(term_priority=priority, local_json_path=str(json_path), enable_semantic_match=False)
    request = SimpleNamespace(run_id="existing-term-update", spec=SimpleNamespace(mode="mixed"), config=config)
    manager = TermDatabaseManager(config, "shared.esp")
    manager.get_dynamic_db().add_many_and_save([("Name", "已有译名", origin, "NPC_:FULL")])
    guard = TaskConsistency(request)
    with guard.terminology_access():
        manager.load_all()
        guard.observe_terms("source", "translation", manager)

    GeneratedTermWriter(manager, access=guard.terminology_write_access).save(
        [("Name", "新译名", "auto_name", "NPC_:FULL")],
        observer=lambda current, terms: guard.record_generated_terms("source", current, terms),
    )

    dynamic_translation = "已有译名" if origin == "manual" else "新译名"
    expected = dynamic_translation if preferred_source == "dynamic" else "文件译名"
    assert manager.get_dynamic_db().as_list()[0].translation == dynamic_translation
    assert manager.resolve_term("Name").translation == expected
    assert manager.exact_match(["Name"]) == {"Name": expected}
    guard.require_current(request)


def test_external_write_after_gate_check_is_rejected_before_task_save(term_task, monkeypatch):
    manager = term_task.load("source")
    database = manager.get_dynamic_db()
    external = TermDatabaseManager(term_task.config, "shared.esp")
    external.load_all()
    path = Path(database._path)
    load = database.load
    external_bytes = []
    observed = []

    def change_file_then_load():
        external.get_dynamic_db().add_many_and_save([("External Name", "外部名称", "manual", "")])
        external_bytes.append(path.read_bytes())
        load()

    monkeypatch.setattr(database, "load", change_file_then_load)
    written = term_task.save(
        "source", manager, [("Task Name", "任务名称", "auto_name", "NPC_:FULL")], lambda *_: observed.append(True)
    )

    assert written == 0
    assert external_bytes
    assert observed == []
    assert path.read_bytes() == external_bytes[-1]
    term_task.guard.require_current(term_task.request)


def test_external_write_after_task_save_is_rejected_during_ownership_registration(term_task):
    manager = term_task.load("source")
    path = Path(manager.get_dynamic_db()._path)
    external_bytes = []

    def change_file_before_registration(_manager, _terms):
        external = TermDatabaseManager(term_task.config, "shared.esp")
        external.load_all()
        assert {entry.term for entry in external.get_dynamic_db().as_list()} == {"Task Name"}
        external.get_dynamic_db().add_many_and_save([("External Name", "外部名称", "manual", "")])
        external_bytes.append(path.read_bytes())

    written = term_task.save(
        "source", manager, [("Task Name", "任务名称", "auto_name", "NPC_:FULL")], change_file_before_registration
    )

    assert written == 0
    assert external_bytes
    assert path.read_bytes() == external_bytes[-1]
    term_task.guard.require_current(term_task.request)
