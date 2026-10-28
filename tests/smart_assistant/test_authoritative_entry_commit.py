import queue
import threading
from types import SimpleNamespace

import pytest

from tests.conftest import make_test_collection
from transbridge.application.contracts import DomainError, ErrorCategory, OperationResult
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


def test_execution_context_captures_target_without_copying_every_entry():
    class UnreadCollection:
        def __iter__(self):
            raise AssertionError("context creation must not scan all entries")

    collection = UnreadCollection()
    app = SimpleNamespace(
        collection=collection,
        active_version_identity=("project", "variant"),
        project_revision=3,
        variant_revision=8,
    )
    context = ExecutionContext(app_context=app)
    assert context._target_collection is collection
    assert context._target_version_identity == ("project", "variant")
    assert context._target_variant_revision == 8


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


@pytest.mark.parametrize(
    ("changed_field", "changed_value", "message"),
    [
        ("active_version_identity", ("project", "other"), "Project/Variant"),
    ],
)
def test_queued_label_mutation_rejects_switched_identity_before_returning_success(
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
