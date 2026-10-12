"""Desktop task navigation backed by feature-owned records and fresh preparation."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from transbridge.application.contracts import JobRef, OperationOutcome
from transbridge.application.tasks import JobState, OwnerRef, TaskCenterAction, TaskNavigationIntent
from transbridge.application.tasks.models import TaskAccessError
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.ui.operations.execution_result import execution_receipt
from transbridge.ui.windowing import show_and_activate

from .action_catalog import IntentId

_AI_TYPES = frozenset({"ai-translate", "ai-polish", "ai-mixed", "ai-custom", "ai-batch"})
_PREPARATION = {
    "operation.write": IntentId.PUBLISH_WRITE,
    "operation.fomod": IntentId.PUBLISH_FOMOD,
    "operation.paratranz.upload": IntentId.SYNC_UPLOAD,
    "operation.paratranz.download": IntentId.SYNC_DOWNLOAD,
    **dict.fromkeys(_AI_TYPES, IntentId.TRANSLATION_AI),
}


class DesktopTaskNavigation:
    """One window's binding; no paths or previous write arguments enter intents."""

    def __init__(self, host, dispatch):
        self._host = host
        self._dispatch = dispatch
        self._dialogs = []
        self._registry = host.app_runtime.use_cases.resolve("task_history_navigators")
        for job_type in _PREPARATION:
            self._registry.register(job_type, self)

    def close(self):
        for job_type in _PREPARATION:
            self._registry.unregister(job_type, self)
        for dialog in tuple(self._dialogs):
            dialog.close()
        self._dialogs.clear()

    def _same_project(self, record) -> bool:
        identity = self._host.context.active_version_identity
        return identity is not None and tuple(map(str, identity)) == (
            str(record.owner.project_id),
            str(record.owner.variant_id),
        )

    def _ai_result_available(self, record) -> bool:
        if not self._same_project(record):
            return False
        registry = self._host.workbench.ai_tasks
        directory = registry.directory_provider()
        return record.run_id in registry.windows or (
            directory is not None and TaskHistoryStore(directory / "ai-task-history").contains(record.run_id)
        )

    def _operation_result(self, record, actor):
        facade = self._host.operation_plan_facade
        if facade is None:
            return None
        try:
            return facade.read_task_result(JobRef(record.job_id, record.owner.owner_id, record.run_id), actor)
        except TaskAccessError as exc:
            if exc.code != "job_not_found":
                raise
            return None

    def resolve(self, record, actor, action):
        if not record.visible_to(actor):
            return None
        if action is TaskCenterAction.OPEN_RESULT:
            available = (
                self._ai_result_available(record)
                if record.job_type in _AI_TYPES
                else self._operation_result(record, actor) is not None
            )
            if available:
                return self._intent(record, "result")
        if action is TaskCenterAction.OPEN_LOG:
            # Only advertise diagnostics actually retained by a terminal record.
            if record.diagnostic_code or (
                record.job_type not in _AI_TYPES
                and (result := self._operation_result(record, actor)) is not None
                and result.diagnostics
            ):
                return self._intent(record, "diagnostics")
        return None

    def prepare_retry(self, record, actor):
        if not record.visible_to(actor) or record.job_type not in _PREPARATION:
            return None
        if record.owner.project_id is not None and not self._same_project(record):
            return None
        if record.owner.project_id is None and record.owner.owner_id != self._host.runtime_context.owner_id:
            return None
        result = None if record.job_type in _AI_TYPES else self._operation_result(record, actor)
        if record.state not in {JobState.FAILED, JobState.CANCELLED} and (
            result is None or result.outcome is not OperationOutcome.PARTIAL
        ):
            return None
        if getattr(self._host, "close_pending", False):
            return None
        if record.job_type != "operation.fomod" and self._host.context.collection is None:
            return None
        return self._intent(record, "prepare")

    @staticmethod
    def _intent(record, action):
        return TaskNavigationIntent(
            f"desktop.task.{action}",
            (("run_id", record.run_id), ("revision", str(record.terminal_revision))),
        )

    def dispatch(self, intent) -> bool:
        if intent.target not in {"desktop.task.result", "desktop.task.diagnostics", "desktop.task.prepare"}:
            return False
        context = self._host.runtime_context
        actor = OwnerRef(context.owner_id, "gui", permissions=frozenset((*context.permissions, "tasks:manage")))
        parameters = dict(intent.parameters)
        history = self._host.app_runtime.use_cases.resolve("task_history")
        record = next((value for value in history.list(actor) if value.run_id == parameters.get("run_id")), None)
        if record is None or str(record.terminal_revision) != parameters.get("revision"):
            raise ValueError("任务记录已变化，请刷新目录。")
        if intent.target == "desktop.task.prepare":
            if self.prepare_retry(record, actor) != intent:
                raise ValueError("工程、版本或任务状态已变化，请返回原内容并刷新目录。")
            actions = self._host.app_runtime.use_cases.resolve("task_center_actions")
            item = next(
                (item for item in actions.list_history(actor, retry_context=None) if item.run_id == record.run_id),
                None,
            )
            if (
                item is None
                or actions.execute(item, TaskCenterAction.RETRY, actor, retry_context=None).navigation != intent
            ):
                raise ValueError("重新准备入口已变化，请刷新目录。")
            self._dispatch(_PREPARATION[record.job_type])
        else:
            action = TaskCenterAction.OPEN_RESULT if intent.target.endswith(".result") else TaskCenterAction.OPEN_LOG
            if self.resolve(record, actor, action) != intent:
                raise ValueError("任务结果或诊断当前不可用，请刷新目录。")
            if action is TaskCenterAction.OPEN_RESULT and record.job_type in _AI_TYPES:
                self._host.workbench.ai_tasks.open_history(run_id=record.run_id)
            else:
                self._show_receipt(record, actor, diagnostics_only=action is TaskCenterAction.OPEN_LOG)
        return True

    def _show_receipt(self, record, actor, *, diagnostics_only):
        result = None if record.job_type in _AI_TYPES else self._operation_result(record, actor)
        text = f"{record.display_name}\n任务 ID：{record.run_id}\n状态：{record.state.value}"
        if record.diagnostic_code:
            text += f"\n诊断：{record.diagnostic_code}"
        if result is not None:
            from transbridge.application.tasks import activity_from_snapshot

            snapshot = self._host.app_runtime.tasks.get(
                JobRef(record.job_id, record.owner.owner_id, record.run_id), actor
            )
            receipt = execution_receipt(
                activity_from_snapshot(snapshot), result, label=record.display_name, is_batch=False
            )
            text += "\n\n" + (receipt.details if diagnostics_only else receipt.text + "\n\n" + receipt.details)
        dialog = QDialog(self._host)
        dialog.setWindowTitle("任务诊断" if diagnostics_only else "任务结果")
        dialog.resize(760, 480)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit(dialog)
        content.setReadOnly(True)
        content.setPlainText(text)
        content.setAccessibleName(dialog.windowTitle())
        layout.addWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda: self._release(dialog))
        show_and_activate(dialog)

    def _release(self, dialog):
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)
        dialog.deleteLater()
