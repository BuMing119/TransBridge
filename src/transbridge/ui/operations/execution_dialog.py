"""Retained operation results and asynchronous execution controls."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialogButtonBox, QPlainTextEdit, QProgressBar, QToolButton

from transbridge.ui.foundation.accessibility import update_accessible_state
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState

from .execution_result import execution_receipt
from .plan_dialog import OperationPlanDialog


class ExecutionPlanDialog(OperationPlanDialog):
    execution_result_changed = pyqtSignal(object, object)
    execution_finished = pyqtSignal()
    execution_status_changed = pyqtSignal(str, str)
    requires_async_preflight = True
    operation_label = "写回"

    def __init__(self, plan, parent=None):
        self._execution_running = False
        self._preflight_running = False
        super().__init__(plan, parent)
        self._close_button = self.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel)
        self._close_button.setText("取消")
        self._checks.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._checks_scroll.setFixedHeight(180)
        self._details_button = QToolButton(self)
        self._details_button.setText("查看条目与技术详情")
        self._details_button.setCheckable(True)
        self._details_button.setAccessibleName("展开或收起条目与技术详情")
        self._details = QPlainTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setFixedHeight(160)
        self._details.setAccessibleName("条目与技术详情")
        self._details_button.toggled.connect(self._details.setVisible)
        self._progress = QProgressBar(self)
        ComponentStyle.apply_static(self._progress, ComponentKind.PROGRESS)
        self._progress.setAccessibleName(f"{self.operation_label}进度")
        for widget in (self._details_button, self._details, self._progress):
            self.layout().insertWidget(self.layout().count() - 1, widget)
            widget.hide()
        self.execution_result_changed.connect(self.render_execution, Qt.ConnectionType.QueuedConnection)

    def render_plan(self, plan):
        super().render_plan(plan)
        self._summary.show()
        if hasattr(self, "_details"):
            self._clear_details()
            self._progress.hide()

    def _clear_details(self):
        self._details_button.setChecked(False)
        self._details_button.hide()
        self._details.hide()
        self._details.clear()

    def set_preflight_running(self, running):
        self._preflight_running = running
        if running:
            self._preflight = None
            self._clear_details()
            self._progress.setRange(0, 0)
            self._set_status("正在检查文件与输出位置，请稍候…", "checking", SemanticState.INFO)
        self._sync_controls()

    def render_preflight(self, result):
        super().render_preflight(result)
        self._sync_controls()

    def _sync_controls(self):
        busy = self._execution_running or self._preflight_running
        self._fields_group.setEnabled(not busy)
        self._preflight_button.setEnabled(not busy)
        self._edit_button.setEnabled(not busy)
        self._close_button.setEnabled(not self._execution_running)
        self._confirm_button.setEnabled(
            not busy
            and self._preflight is not None
            and self._preflight.ready
            and self._preflight.confirmation_token is not None
        )
        self._progress.setVisible(busy)

    def set_execution_running(self, running):
        self._execution_running = running
        self._preflight = None
        for editor in self._field_edits.values():
            editor.setEnabled(False)
        self._sync_controls()
        if running:
            self._clear_details()
            self._progress.setRange(0, 0)
            text = f"正在{self.operation_label}，请稍候…"
            self._set_status(text, "running", SemanticState.INFO)
            self.execution_status_changed.emit("running", text)

    def render_preflight_error(self, message):
        self._preflight = None
        self._confirm_button.setEnabled(False)
        self._set_status(message, "failed", SemanticState.ERROR)
        self.execution_status_changed.emit("waiting", f"尚未开始：{message}")

    def render_execution(self, activity, result):
        if not self._execution_running or not activity.is_terminal:
            return
        self.set_execution_running(False)
        self._close_button.setText("关闭")
        receipt = execution_receipt(activity, result, label=self.operation_label, is_batch=self._plan.is_batch)
        semantic = {"completed": SemanticState.SUCCESS, "failed": SemanticState.ERROR}.get(
            receipt.status, SemanticState.WARNING
        )
        self._summary.hide()
        self._details.setPlainText(receipt.details)
        self._details_button.show()
        self._set_status(receipt.text, receipt.status, semantic)
        if receipt.status == "completed":
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
            self._progress.show()
        self.execution_status_changed.emit(receipt.status, receipt.text)
        self.execution_finished.emit()

    def _set_status(self, text, status, semantic):
        self._checks.setText(text)
        self._checks.setProperty("tbStatusId", status)
        update_accessible_state(self._checks, text)
        ComponentStyle.apply_state(self._checks, semantic)
        self._checks_scroll.verticalScrollBar().setValue(0)

    def _request_preflight(self):
        if not self._execution_running and not self._preflight_running:
            super()._request_preflight()

    def _return_to_edit(self):
        if not self._execution_running and not self._preflight_running:
            super()._return_to_edit()

    def _confirm(self):
        if not self._execution_running and not self._preflight_running:
            super()._confirm()

    def done(self, result):
        if not self._execution_running:
            super().done(result)

    def closeEvent(self, event):  # noqa: N802 - Qt override
        if self._execution_running:
            event.ignore()
        else:
            super().closeEvent(event)


class FomodPlanDialog(ExecutionPlanDialog):
    operation_label = "构建"

    def __init__(self, plan, _context=None, parent=None):
        super().__init__(plan, parent)
