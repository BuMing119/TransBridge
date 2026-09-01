from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from tests.conftest import MockAppContext, make_entry
from transbridge.application.tasks import JobState
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess


@pytest.fixture
def manager():
    TaskManager.reset()
    yield TaskManager()
    TaskManager.reset()


def test_runtime_cancel_reaches_registered_worker_and_rejects_late_commit(manager):
    entered = threading.Event()
    release = threading.Event()
    mutations = []
    decisions = []
    task_id = manager.register()
    handle = manager.get_handle(task_id)
    binding = handle.execution

    def work():
        entered.set()
        assert release.wait(3)
        decisions.append(binding.commit(task_id, lambda: mutations.append("late result")))

    worker = manager.start_thread(task_id, work)
    try:
        assert entered.wait(3)
        manager.runtime.pause(binding.ref, binding.owner)
        assert not handle.pause_event.is_set()
        manager.runtime.cancel(binding.ref, binding.owner)
        assert handle.stop_event.is_set() and handle.pause_event.is_set()
        assert manager.runtime.get(binding.ref, binding.owner).state is JobState.CANCELLING
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert manager.runtime.get(binding.ref, binding.owner).state is JobState.CANCELLED
    assert mutations == []
    assert len(decisions) == 1 and not decisions[0].accepted


def test_runtime_shutdown_joins_registered_compatibility_worker(manager):
    entered = threading.Event()
    task_id = manager.register()
    handle = manager.get_handle(task_id)

    def work():
        entered.set()
        assert handle.stop_event.wait(3)

    worker = manager.start_thread(task_id, work)
    assert entered.wait(3)
    result = manager.runtime.shutdown(grace=3)
    assert result.joined == (handle.execution.ref,)
    assert result.timed_out == () and result.backend_released
    assert not worker.is_alive()
    assert manager.get_status(task_id)["status"] == "cancelled"


class RecordingCollection(TranslationEntryCollection):
    def __init__(self, entries):
        super().__init__(entries)
        self.applied = []

    def apply(self, change_set, context):
        result = super().apply(change_set, context)
        self.applied.append(result)
        return result


@pytest.mark.parametrize("cancel", [False, True])
def test_real_postprocess_respects_task_center_controls_before_candidate_commit(manager, monkeypatch, tmp_path, cancel):
    from transbridge.smart_assistant.tools import tool_proofreader

    entered = threading.Event()
    release = threading.Event()
    response_returned = threading.Event()

    class Client:
        def chat(self, messages, max_tokens=0):
            entered.set()
            assert release.wait(3)
            payload = json.loads(messages[-1]["content"])
            response_returned.set()
            return json.dumps({
                "results": [
                    {"entry_key": item["entry_key"], "final_translation": "corrected"} for item in payload["entries"]
                ]
            })

    monkeypatch.setattr(
        "transbridge.smart_assistant.tools._common.load_llm_config",
        lambda: LLMConfig(api_key="fixture", model="fixture"),
    )
    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda *_args: Client())
    monkeypatch.setattr(
        "transbridge.ai_translator.term_database.TermDatabaseManager",
        lambda **_kwargs: SimpleNamespace(load_all=lambda: None, match_terms=lambda _texts: {}),
    )
    monkeypatch.setattr("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(tool_proofreader, "_resolve_report_directory", lambda _ctx: tmp_path / "reports")
    collection = RecordingCollection([make_entry("entry", original="Source", translation="before", stage=1)])
    context = MockAppContext(collection)
    result = _tool_run_postprocess({}, context)
    assert result.success, result.message
    task_id = result.data["task_id"]
    handle = manager.get_handle(task_id)
    binding = handle.execution
    try:
        assert entered.wait(3)
        manager.runtime.pause(binding.ref, binding.owner)
        if cancel:
            manager.runtime.cancel(binding.ref, binding.owner)
            assert manager.get_status(task_id)["status"] == "cancelling"
        release.set()
        assert response_returned.wait(3)
        if not cancel:
            # The real candidate pipeline must park at the guard, without mutating.
            handle._thread.join(0.15)
            assert handle._thread.is_alive()
            assert manager.get_status(task_id)["status"] == "paused"
            assert collection.applied == []
            assert collection.get("entry").translation == "before"
            manager.runtime.resume(binding.ref, binding.owner)
    finally:
        release.set()
        if manager.get_status(task_id)["status"] == "paused":
            manager.cancel(task_id)
        handle._thread.join(5)
    assert not handle._thread.is_alive()
    if cancel:
        assert manager.get_status(task_id)["status"] == "cancelled"
        assert collection.applied == []
        assert collection.get("entry").translation == "before"
    else:
        assert manager.get_status(task_id)["status"] == "completed", manager.get_status(task_id)
        assert len(collection.applied) == 1
        assert collection.get("entry").translation == "corrected"
