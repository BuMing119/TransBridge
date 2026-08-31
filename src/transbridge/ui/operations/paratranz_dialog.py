"""Task-oriented ParaTranz upload/download confirmation dialog."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from transbridge.paratranz.project_catalog import ParaTranzProjectCatalog
from transbridge.ui.foundation.accessibility import configure_accessible_widget, update_accessible_state
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState, configure_dialog
from transbridge.ui.workbench.remote_target_view import ParaTranzTargetDialog

from .plan_view import EditableFieldState, OperationKind, OperationPlanViewState
from .preflight_view import OperationPreflightResult, PreflightCheckStatus


class ParaTranzSyncDialog(QDialog):
    """A single-action sync dialog that automatically refreshes its preflight."""

    preflight_requested = pyqtSignal(str, object)
    return_to_edit_requested = pyqtSignal(str, object)
    confirm_requested = pyqtSignal(str, object)

    def __init__(self, plan: OperationPlanViewState, context: object, parent=None) -> None:
        super().__init__(parent)
        configure_dialog(self)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(False)
        self.setMinimumWidth(590)
        self.resize(640, 590)
        self._context = context
        self._catalog = ParaTranzProjectCatalog()
        self._plan = plan
        self._preflight: OperationPreflightResult | None = None
        self._project_id = ""
        self._project_name = ""
        self._rendering = False
        self._preflight_scheduled = False
        self._strategy_buttons: dict[str, QRadioButton] = {}

        self.setWindowTitle(plan.title)
        configure_accessible_widget(self, name=plan.title, description="选择云端项目和同步方式，然后确认执行")
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        self._headline = QLabel(plan.title, self)
        headline_font = self._headline.font()
        headline_font.setPointSize(headline_font.pointSize() + 3)
        headline_font.setBold(True)
        self._headline.setFont(headline_font)
        root.addWidget(self._headline)

        self._scope = QLabel(self)
        self._scope.setWordWrap(True)
        root.addWidget(self._scope)

        target_card = QFrame(self)
        ComponentStyle.apply_static(target_card, ComponentKind.CARD)
        target_layout = QHBoxLayout(target_card)
        target_text = QVBoxLayout()
        target_caption = QLabel("ParaTranz 项目", target_card)
        self._target_name = QLabel(target_card)
        self._target_name.setWordWrap(True)
        target_font = self._target_name.font()
        target_font.setBold(True)
        self._target_name.setFont(target_font)
        self._target_detail = QLabel(target_card)
        target_text.addWidget(target_caption)
        target_text.addWidget(self._target_name)
        target_text.addWidget(self._target_detail)
        target_layout.addLayout(target_text, 1)
        self._choose_project = QPushButton("更换项目…", target_card)
        ComponentStyle.apply_static(self._choose_project, ComponentKind.BUTTON)
        self._choose_project.clicked.connect(self._choose_remote_project)
        target_layout.addWidget(self._choose_project)
        root.addWidget(target_card)

        strategy_caption = QLabel("同步方式", self)
        strategy_font = strategy_caption.font()
        strategy_font.setBold(True)
        strategy_caption.setFont(strategy_font)
        root.addWidget(strategy_caption)
        self._strategy_layout = QVBoxLayout()
        self._strategy_group = QButtonGroup(self)
        self._strategy_group.buttonToggled.connect(
            lambda _button, checked: self._controls_changed() if checked else None
        )
        root.addLayout(self._strategy_layout)

        self._set_default = QCheckBox("以后默认使用这个云端项目", self)
        self._set_default.toggled.connect(self._controls_changed)
        root.addWidget(self._set_default)
        self._apply_deletions = QCheckBox("同时删除云端已明确删除的本地条目", self)
        self._apply_deletions.toggled.connect(self._controls_changed)
        root.addWidget(self._apply_deletions)

        self._backup = QLabel(self)
        self._backup.setWordWrap(True)
        ComponentStyle.apply_static(self._backup, ComponentKind.NOTIFICATION)
        ComponentStyle.apply_state(self._backup, SemanticState.INFO)
        root.addWidget(self._backup)

        impact_caption = QLabel("预计影响", self)
        impact_font = impact_caption.font()
        impact_font.setBold(True)
        impact_caption.setFont(impact_font)
        root.addWidget(impact_caption)
        self._impact = QLabel("正在计算…", self)
        self._impact.setWordWrap(True)
        root.addWidget(self._impact)

        self._status = QLabel("正在检查项目权限和云端内容…", self)
        self._status.setWordWrap(True)
        ComponentStyle.apply_static(self._status, ComponentKind.NOTIFICATION)
        ComponentStyle.apply_state(self._status, SemanticState.INFO)
        configure_accessible_widget(self._status, name="同步检查状态", state_text=self._status.text())
        root.addWidget(self._status)
        root.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._cancel = QPushButton("取消", self)
        self._confirm = QPushButton(self._action_text(plan.kind), self)
        for button in (self._cancel, self._confirm):
            ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        ComponentStyle.apply_state(self._confirm, SemanticState.PRIMARY)
        self._confirm.setEnabled(False)
        self._confirm.setDefault(True)
        self._cancel.clicked.connect(self.reject)
        self._confirm.clicked.connect(self._confirm_operation)
        footer.addWidget(self._cancel)
        footer.addWidget(self._confirm)
        root.addLayout(footer)

        self.render_plan(plan)
        QTimer.singleShot(0, self._request_preflight)

    def render_plan(self, plan: OperationPlanViewState) -> None:
        self._rendering = True
        try:
            self._plan = plan
            self._preflight = None
            self.setWindowTitle(plan.title)
            self._headline.setText(plan.title)
            self._scope.setText(plan.scope_summary)
            fields = {field.field_id: field for field in plan.editable_fields}
            project = fields.get("paratranz_project_id")
            if project is not None:
                self._project_id = project.value
                self._project_name = project.display_value or "尚未选择云端项目"
            self._target_name.setText(self._project_name)
            self._target_detail.setText(self._target_detail_text(plan.target))
            self._backup.setText(f"保护措施：{plan.backup_summary}")
            self._render_strategy(fields.get("conflict_policy"))
            self._render_checkbox(self._set_default, fields.get("set_as_default"))
            self._render_checkbox(self._apply_deletions, fields.get("apply_remote_deletions"))
            self._apply_deletions.setVisible(plan.kind is OperationKind.DOWNLOAD)
            self._confirm.setText(self._action_text(plan.kind))
            self._confirm.setEnabled(False)
            self._impact.setText("正在计算…")
            self._set_status("正在检查项目权限和云端内容…", SemanticState.INFO)
        finally:
            self._rendering = False

    def render_preflight(self, result: OperationPreflightResult) -> None:
        if result.request_digest != self._plan.request_digest:
            return
        self._preflight = result
        impact = dict(result.estimated_impact)
        self._impact.setText(self._impact_text(impact))
        blocked = [item.reason for item in result.checks if item.status is PreflightCheckStatus.BLOCKED]
        warnings = [
            item.reason for item in result.checks if item.status is PreflightCheckStatus.WARNING and item.reason
        ]
        actionable = sum(
            impact.get(key, 0)
            for key in (
                "create_local",
                "update_local",
                "delete_local",
                "create_remote",
                "update_remote",
                "delete_remote",
            )
        )
        if blocked:
            self._set_status(blocked[0], SemanticState.ERROR)
        elif actionable == 0:
            self._set_status(
                "本地内容与 ParaTranz 已一致，无需更新。"
                if self._plan.kind is OperationKind.DOWNLOAD
                else "ParaTranz 内容与本地已一致，无需上传。",
                SemanticState.SUCCESS,
            )
        elif warnings:
            self._set_status(warnings[0], SemanticState.WARNING)
        else:
            self._set_status("检查完成，可以开始。", SemanticState.SUCCESS)
        self._confirm.setEnabled(result.ready and result.confirmation_token is not None and actionable > 0)

    def render_preflight_error(self, message: str) -> None:
        self._preflight = None
        self._confirm.setEnabled(False)
        if "changed while preflight" in message or "PREFLIGHT_STALE" in message:
            self._set_status("选项已变化，正在按最新设置重新检查…", SemanticState.INFO)
            return
        self._set_status(str(message) or "检查失败，请稍后重试。", SemanticState.ERROR)

    def set_preflight_running(self, running: bool) -> None:
        self._choose_project.setEnabled(not running)
        self._confirm.setEnabled(False if running else self._confirm.isEnabled())
        if running:
            self._set_status("正在检查项目权限和云端内容…", SemanticState.INFO)

    def edited_values(self) -> tuple[tuple[str, str], ...]:
        selected = self._strategy_group.checkedButton()
        policy = "" if selected is None else str(selected.property("policyValue") or "")
        return (
            ("paratranz_project_id", self._project_id),
            ("paratranz_project_name", self._project_name),
            ("set_as_default", "true" if self._set_default.isChecked() else "false"),
            ("conflict_policy", policy),
            ("apply_remote_deletions", "true" if self._apply_deletions.isChecked() else "false"),
        )

    def _render_strategy(self, field: EditableFieldState | None) -> None:
        if field is None:
            return
        if tuple(self._strategy_buttons) != tuple(value for value, _label in field.options):
            while self._strategy_layout.count():
                item = self._strategy_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    self._strategy_group.removeButton(widget)
                    widget.deleteLater()
            self._strategy_buttons.clear()
            for value, label in field.options:
                button = QRadioButton(label, self)
                button.setProperty("policyValue", value)
                self._strategy_group.addButton(button)
                self._strategy_layout.addWidget(button)
                self._strategy_buttons[value] = button
        selected = self._strategy_buttons.get(field.value)
        if selected is not None:
            selected.setChecked(True)

    @staticmethod
    def _render_checkbox(widget: QCheckBox, field: EditableFieldState | None) -> None:
        if field is None:
            widget.hide()
            return
        widget.setText(field.label)
        widget.setChecked(field.value.strip().casefold() == "true")
        widget.setEnabled(field.enabled)
        widget.setVisible(field.enabled)
        widget.setToolTip(field.help_text)

    def _controls_changed(self, *_args) -> None:
        if self._rendering or self._preflight_scheduled:
            return
        self._preflight = None
        self._confirm.setEnabled(False)
        self._preflight_scheduled = True
        QTimer.singleShot(0, self._request_preflight)

    def _request_preflight(self) -> None:
        self._preflight_scheduled = False
        self.preflight_requested.emit(self._plan.session_id, self.edited_values())

    def _confirm_operation(self) -> None:
        token = None if self._preflight is None else self._preflight.confirmation_token
        if token is not None:
            self.confirm_requested.emit(self._plan.session_id, token)

    def _choose_remote_project(self) -> None:
        config_revision = int(getattr(getattr(self._context, "config", None), "config_revision", 0))
        dialog = ParaTranzTargetDialog(
            self._context,
            self._catalog,
            config_revision,
            self,
            explanation="选择本次同步使用的云端项目。项目编号只用于内部识别。",
            confirmation_text="使用这个项目",
        )
        if not dialog.exec():
            return
        project = dialog.selected_project()
        if project is None:
            return
        self._project_id = str(project["id"])
        self._project_name = str(project["name"])
        self._target_name.setText(self._project_name)
        self._target_detail.setText("本次选择")
        self._set_default.setVisible(getattr(self._context, "active_project_id", None) is not None)
        self._set_default.setEnabled(self._set_default.isVisible())
        self._controls_changed()

    def _set_status(self, text: str, state: SemanticState) -> None:
        self._status.setText(text)
        update_accessible_state(self._status, text)
        ComponentStyle.apply_state(self._status, state)

    def _impact_text(self, impact: dict[str, int]) -> str:
        if self._plan.kind is OperationKind.DOWNLOAD:
            parts = (
                ("更新", impact.get("update_local", 0)),
                ("新增", impact.get("create_local", 0)),
                ("删除", impact.get("delete_local", 0)),
                ("保留", impact.get("skip", 0)),
                ("需处理", impact.get("conflict", 0)),
            )
        else:
            parts = (
                ("更新", impact.get("update_remote", 0)),
                ("新增", impact.get("create_remote", 0)),
                ("删除", impact.get("delete_remote", 0)),
                ("保留", impact.get("skip", 0)),
                ("需处理", impact.get("conflict", 0)),
            )
        return "  ·  ".join(f"{label} {value:,}" for label, value in parts if value) or "没有内容变化"

    @staticmethod
    def _target_detail_text(target: str) -> str:
        if "当前工程已绑定" in target:
            return "当前工程已绑定"
        if "本次选择" in target:
            return "本次选择"
        return "尚未绑定到当前工程"

    @staticmethod
    def _action_text(kind: OperationKind) -> str:
        return "下载并更新本地" if kind is OperationKind.DOWNLOAD else "上传到 ParaTranz"


__all__ = ["ParaTranzSyncDialog"]
