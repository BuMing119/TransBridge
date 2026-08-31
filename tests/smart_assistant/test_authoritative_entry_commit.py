import queue
import threading
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.smart_assistant.tools.tool_editor import _tool_manage_entry_labels
from transbridge.smart_assistant.tools.types import ExecutionContext


class _Signal:
    def __init__(self) -> None:
        self.values = []

    def emit(self, value) -> None:
        self.values.append(value)


class _Commands:
    def __init__(self, result=None) -> None:
        self.result = result or OperationResult.completed({"revision": 6})
        self.calls = []

    def replace_entry_states(self, states, context, **expected):
        self.calls.append((states, context, expected))
        return self.result


def _app_context(commands):
    return SimpleNamespace(
        collection=make_test_collection(2),
        uses_authoritative_projection=True,
        active_version_identity=("project", "variant"),
        project_revision=4,
        variant_revision=5,
        project_commands=commands,
        runtime_context=object(),
        collection_changed=_Signal(),
    )


def test_assistant_notification_commits_authoritative_entry_states() -> None:
    commands = _Commands()
    app_context = _app_context(commands)
    context = ExecutionContext(app_context=app_context)
    entry = next(iter(app_context.collection))
    entry.translation = "助手译文"

    context.notify_collection_modified()

    states, _runtime, expected = commands.calls[0]
    assert next(iter(states.values())) == ("助手译文", 0)
    assert expected["expected_project_revision"] == 4
    assert expected["expected_variant_revision"] == 5
    assert app_context.collection_changed.values == [app_context.collection]


def test_assistant_notification_restores_last_committed_projection_on_conflict() -> None:
    failed = OperationResult.failed(DomainError(ErrorCategory.CONFLICT, "STALE", "版本已变化"))
    commands = _Commands(failed)
    app_context = _app_context(commands)
    context = ExecutionContext(app_context=app_context)
    entry = next(iter(app_context.collection))
    before = entry.translation
    entry.translation = "不应保留"

    with pytest.raises(RuntimeError, match="版本已变化"):
        context.notify_collection_modified()

    assert entry.translation == before


def test_assistant_rollback_rebuilds_visible_entries_from_latest_authority() -> None:
    commands = _Commands()
    app_context = _app_context(commands)
    entry = next(iter(app_context.collection))
    projection = ProjectionStore(
        ProjectionSnapshot(
            "project:project",
            7,
            7,
            {
                "project_id": "project",
                "variant_id": "variant",
                "entries": [
                    {
                        "entry_key": entry.identity.to_dict(),
                        "translation": "并发授权译文",
                        "stage": 3,
                    }
                ],
            },
        )
    )
    app_context._project_projection = projection
    app_context.safe_mutate = lambda callback: callback()
    context = ExecutionContext(app_context=app_context)
    before = context.capture_entry_states()
    entry.translation = "助手部分结果"
    entry.stage = 1

    context.rollback_entry_states(before)

    assert (entry.translation, entry.stage) == ("并发授权译文", 3)


@pytest.mark.parametrize(
    ("changed_field", "changed_value", "message"),
    [
        ("active_version_identity", ("project", "other"), "Project/Variant"),
        ("project_revision", 9, "Project 修订号"),
        ("variant_revision", 9, "Variant 修订号"),
    ],
)
def test_queued_label_mutation_rejects_stale_authority_before_returning_success(
    changed_field: str,
    changed_value: object,
    message: str,
) -> None:
    callbacks: queue.Queue = queue.Queue()
    app_context = _app_context(_Commands())
    app_context.entry_labels = {}
    app_context.label_library = {}
    app_context.replace_calls = 0
    app_context.safe_mutate = callbacks.put

    def replace_projected_labels(_entry_labels, _label_library, **_expected):
        app_context.replace_calls += 1
        return OperationResult.completed({"project_revision": 4, "revision": 6})

    app_context.replace_projected_labels = replace_projected_labels
    context = ExecutionContext(app_context=app_context)
    setattr(app_context, changed_field, changed_value)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(_tool_manage_entry_labels({"action": "create", "name": "审校"}, context))
    )

    worker.start()
    callbacks.get(timeout=1)()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert len(results) == 1
    assert not results[0].success
    assert message in results[0].message
    assert app_context.replace_calls == 0


def test_queued_label_command_failure_is_reported_to_the_tool_caller() -> None:
    callbacks: queue.Queue = queue.Queue()
    failed = OperationResult.failed(DomainError(ErrorCategory.CONFLICT, "STALE", "标签命令冲突"))
    app_context = _app_context(_Commands())
    app_context.entry_labels = {}
    app_context.label_library = {}
    app_context.safe_mutate = callbacks.put
    expected = []

    def replace_projected_labels(_entries, _library, **values):
        expected.append(values)
        return failed

    app_context.replace_projected_labels = replace_projected_labels
    context = ExecutionContext(app_context=app_context)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(_tool_manage_entry_labels({"action": "create", "name": "审校"}, context))
    )

    worker.start()
    callbacks.get(timeout=1)()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert len(results) == 1
    assert not results[0].success
    assert "标签命令冲突" in results[0].message
    assert expected[0]["expected_project_revision"] == 4
    assert expected[0]["expected_variant_revision"] == 5
    assert expected[0]["expected_variant_ref"].identity.value == "variant"


def test_stopped_assistant_translation_rolls_back_partial_visible_results(monkeypatch) -> None:
    from transbridge.smart_assistant.tools.task_manager import TaskManager
    from transbridge.smart_assistant.tools.tool_translator import _tool_start_translation

    collection = make_test_collection(1)
    entry = next(iter(collection))
    before = (entry.translation, entry.stage)
    app_context = SimpleNamespace(
        collection=collection,
        uses_authoritative_projection=False,
        active_version_identity=None,
        esp_path=None,
        config=SimpleNamespace(token=""),
        entry_labels={},
        label_library={},
        collection_changed=_Signal(),
        safe_mutate=lambda callback: callback(),
        mark_dirty=lambda: None,
    )
    context = ExecutionContext(app_context=app_context)

    class Translator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def translate(self, *, collection, stop_event, **_kwargs):
            target = next(iter(collection))
            target.translation = "不应保留的部分结果"
            target.stage = 1
            stop_event.set()
            return SimpleNamespace(success_count=1, failed_count=0, skipped_count=0)

    llm_config = SimpleNamespace(api_key="test-key", term_priority=["dynamic"])
    monkeypatch.setattr("transbridge.paratranz.config_manager.LLMConfig.load_from_file", lambda: llm_config)
    monkeypatch.setattr("transbridge.ai_translator.translator.AutoTranslator", Translator)
    monkeypatch.setattr("transbridge.ai_translator.translator.TranslatorConfig", lambda **kwargs: kwargs)
    monkeypatch.setattr(TaskManager, "start_thread", lambda _self, _task_id, target: target())
    TaskManager.reset()
    try:
        result = _tool_start_translation({"mode": "translate", "entry_ids": [entry.key]}, context)
        status = TaskManager().get_status(result.data["task_id"])
    finally:
        TaskManager.reset()

    assert result.success
    assert status["status"] == "cancelled"
    assert (entry.translation, entry.stage) == before
