"""Business outcomes remain clear across retained dialogs and the task center."""

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.operations.test_production_facade import _Clock, _Ids
from transbridge.application.contracts import Diagnostic, OperationCounts, OperationOutcome, OperationResult
from transbridge.application.io.operation_write import _operation_result
from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
from transbridge.application.tasks.activity import activity_from_snapshot
from transbridge.ui.operations.plan_view import OperationKind, OperationPlanViewState
from transbridge.ui.operations.runtime_adapter import OperationTaskAdapter, OperationTaskRequest
from transbridge.ui.operations.write_dialog import WritePlanDialog
from transbridge.ui.shell.task_center import TaskCenterPanel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _render(result, *, batch=False):
    runtime = TaskRuntime(id_generator=_Ids(), clock=_Clock(), backend=CallbackThreadBackend(lambda _, work: work()))
    owner = OwnerRef("review", "gui.operation-plan")
    adapter = OperationTaskAdapter(runtime)
    ref = adapter.submit(
        OperationTaskRequest(OperationKind.WRITE, "a" * 64, "source", "写回翻译文件", lambda _: result, True), owner
    )
    activity = activity_from_snapshot(runtime.get(ref, owner))
    plan = OperationPlanViewState(
        "review",
        1,
        OperationKind.WRITE,
        "写回计划",
        "output.esp",
        "当前文件",
        "导出",
        "确认覆盖",
        "覆盖前备份",
        (),
        request_digest="a" * 64,
        is_batch=batch,
    )
    dialog = WritePlanDialog(plan)
    dialog.show()
    dialog.set_execution_running(True)
    dialog.render_execution(activity, adapter.result(ref, owner))
    return dialog, activity


def test_partial_publication_never_claims_the_file_is_complete(qapp):
    result = _operation_result(
        SimpleNamespace(
            outcome=OperationOutcome.PARTIAL,
            target_path="output.esp",
            published=True,
            code="DIRECTORY_FSYNC_FAILED",
            message="artifact durability uncertain (OSError)",
            manifest=None,
            backup_path="output.backup.esp",
        ),
        "write",
    )
    assert result.value["outcomes"][0]["status"] == "failed"
    dialog, activity = _render(result)
    panel = TaskCenterPanel()
    try:
        text = dialog._checks.text()
        assert "部分完成" in text
        assert "未能确认已安全保存" in text
        assert "已完成" not in text
        assert "output.backup.esp" in text
        assert text.count("\noutput.esp\n") == 1
        assert "DIRECTORY_FSYNC_FAILED" not in text
        assert "artifact durability uncertain" in dialog._details.toPlainText()
        panel.render_activity(activity)
        assert panel._current.item(0).text().endswith("部分完成")
        # A worker returning a receipt has stopped; this does not mean every file succeeded.
        assert activity.state.value == "completed"
    finally:
        dialog.close()
        panel.close()


def test_batch_retry_explains_full_scope_and_preserves_recovery(qapp):
    result = OperationResult.partial(
        {
            "outcomes": (
                {"object_ref": "good.esp", "status": "succeeded", "code": "PUBLISHED"},
                {"object_ref": "bad.esp", "status": "failed", "code": "ROLLBACK_FAILED"},
            )
        },
        counts=OperationCounts(succeeded=1, failed=1),
        artifact_refs=("good.esp",),
        diagnostics=(
            Diagnostic(
                "ROLLBACK_FAILED", "恢复旧文件失败，请保留恢复目录。", details=(("recovery_directory", "recovery/bad"),)
            ),
        ),
    )
    dialog, _ = _render(result, batch=True)
    try:
        text = dialog._checks.text()
        assert "已完成 1 个文件，未完整完成 1 个" in text
        assert "全部文件，包括已完成的文件" in text
        assert "关闭后逐个写回" in text
        assert "恢复旧文件失败" in text and "recovery/bad" in text
        assert text.count("good.esp") == 1
        assert "bad.esp：未完整完成" in text
        assert "ROLLBACK_FAILED" in dialog._details.toPlainText()
    finally:
        dialog.close()


def test_completed_task_center_state_is_localized_and_not_partial(qapp):
    dialog, activity = _render(OperationResult.completed(artifact_refs=("out.esp",)))
    panel = TaskCenterPanel()
    try:
        panel.render_activity(activity)
        assert panel._current.item(0).text().endswith("已完成")
        assert "写回完成" in dialog._checks.text()
        assert "可以关闭此窗口" not in dialog._checks.text()
        assert "重试" not in dialog._checks.text()
        assert dialog._close_button.isEnabled()
        panel.render_activity(replace(activity, progress=(("operation_outcome", "partial"),)))
        assert panel._current.item(0).text().endswith("部分完成")
    finally:
        dialog.close()
        panel.close()
