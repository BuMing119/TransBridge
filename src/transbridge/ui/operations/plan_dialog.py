"""One shared Qt presentation for upload/download/write/FOMOD plans."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from .plan_view import OperationPlanViewState
from .preflight_view import OperationPreflightResult, PreflightCheckStatus


class OperationPlanDialog(QDialog):
    """Presentation only: feature coordinators own draft mapping and submit."""

    preflight_requested = pyqtSignal(str, object)
    return_to_edit_requested = pyqtSignal(str, object)
    confirm_requested = pyqtSignal(str, object)

    def __init__(self, plan: OperationPlanViewState, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)
        self._plan = plan
        self._preflight: OperationPreflightResult | None = None
        self._field_edits: dict[str, QLineEdit] = {}
        self.setWindowTitle(plan.title)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        self._fields_group = QGroupBox("计划选项")
        self._fields = QFormLayout(self._fields_group)
        layout.addWidget(self._fields_group)
        self._checks = QLabel("尚未预检")
        self._checks.setWordWrap(True)
        layout.addWidget(self._checks)
        self._preflight_button = QPushButton("运行预检")
        self._edit_button = QPushButton("返回编辑")
        self._confirm_button = QPushButton("确认并开始")
        self._confirm_button.setEnabled(False)
        layout.addWidget(self._preflight_button)
        layout.addWidget(self._edit_button)
        layout.addWidget(self._confirm_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._preflight_button.clicked.connect(self._request_preflight)
        self._edit_button.clicked.connect(self._return_to_edit)
        self._confirm_button.clicked.connect(self._confirm)
        self.render_plan(plan)

    def render_plan(self, plan: OperationPlanViewState) -> None:
        self._plan = plan
        self._preflight = None
        self._confirm_button.setEnabled(False)
        self._checks.setText(plan.submit_disabled_reason or "尚未预检")
        self._summary.setText(
            f"目标：{plan.target}\n"
            f"范围：{plan.scope_summary}\n"
            f"模式：{plan.mode_summary}\n"
            f"冲突：{plan.conflict_summary}\n"
            f"备份：{plan.backup_summary}"
        )
        while self._fields.rowCount():
            self._fields.removeRow(0)
        self._field_edits.clear()
        for field in plan.editable_fields:
            editor = QLineEdit(field.value)
            editor.setEnabled(field.enabled)
            self._fields.addRow(field.label, editor)
            self._field_edits[field.field_id] = editor
        self._fields_group.setVisible(bool(self._field_edits))

    def render_preflight(self, result: OperationPreflightResult) -> None:
        if result.request_digest != self._plan.request_digest:
            raise ValueError("cannot render a preflight for a different plan")
        self._preflight = result
        lines = []
        icons = {
            PreflightCheckStatus.PASSED: "✓",
            PreflightCheckStatus.WARNING: "!",
            PreflightCheckStatus.BLOCKED: "×",
        }
        for item in result.checks:
            suffix = f"：{item.reason}" if item.reason else ""
            lines.append(f"{icons[item.status]} {item.label}{suffix}")
        self._checks.setText("\n".join(lines) or "✓ 预检通过")
        self._confirm_button.setEnabled(result.ready and result.confirmation_token is not None)
        for editor in self._field_edits.values():
            editor.setEnabled(False)

    def edited_values(self) -> tuple[tuple[str, str], ...]:
        return tuple((field_id, editor.text()) for field_id, editor in self._field_edits.items())

    def _request_preflight(self) -> None:
        self.preflight_requested.emit(self._plan.session_id, self.edited_values())

    def _return_to_edit(self) -> None:
        self._preflight = None
        self._confirm_button.setEnabled(False)
        for editor in self._field_edits.values():
            editor.setEnabled(True)
        self.return_to_edit_requested.emit(self._plan.session_id, self.edited_values())

    def _confirm(self) -> None:
        token = None if self._preflight is None else self._preflight.confirmation_token
        if token is not None:
            self.confirm_requested.emit(self._plan.session_id, token)
