"""Task-oriented ParaTranz upload/download confirmation dialog."""

from __future__ import annotations

import json

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from transbridge.application.contracts import OperationResult
from transbridge.application.tasks.activity import TaskActivityViewState
from transbridge.paratranz.project_catalog import ParaTranzProjectCatalog
from transbridge.ui.foundation.accessibility import configure_accessible_widget, update_accessible_state
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, SemanticState, configure_dialog
from transbridge.ui.workbench.remote_target_view import ParaTranzTargetDialog

from .plan_view import EditableFieldState, OperationKind, OperationPlanViewState
from .preflight_view import OperationPreflightResult
from .sync_preflight_presentation import present_sync_preflight
from .sync_result import sync_receipt
from .sync_review_dialog import SyncReviewDialog
from .sync_review_direction import sync_review_direction
from .sync_review_view import SyncReviewRow


class ParaTranzSyncDialog(QDialog):
    """Choose sync options, compare explicitly, then confirm the reviewed changes."""

    preflight_requested = pyqtSignal(str, object)
    return_to_edit_requested = pyqtSignal(str, object)
    confirm_requested = pyqtSignal(str, object)
    execution_changed = pyqtSignal(object)
    execution_result_changed = pyqtSignal(object, object)
    execution_finished = pyqtSignal()
    plan_input_changed = pyqtSignal()

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
        self._needs_comparison = True
        self._preflight_running = False
        self._execution_running = False
        self._check_ready = False
        self._retry_available = False
        self._review_rows: tuple[SyncReviewRow, ...] = ()
        self._sync_decisions: dict[str, str] = {}
        self._review_needs_refresh = False
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

        impact_caption = QLabel("这次会改变什么", self)
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
        self._review = QPushButton("确认这几条翻译…", self)
        self._review.clicked.connect(self._review_items)
        root.addWidget(self._review)
        self._progress = QProgressBar(self)
        ComponentStyle.apply_static(self._progress, ComponentKind.PROGRESS)
        configure_accessible_widget(self._progress, name="同步进度")
        self._progress.hide()
        root.addWidget(self._progress)
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
        for label in (self._headline, self._scope, self._target_name, self._target_detail, self._backup, self._status):
            label.setTextFormat(Qt.TextFormat.PlainText)

        self.execution_changed.connect(self.render_execution, Qt.ConnectionType.QueuedConnection)
        self.execution_result_changed.connect(self.render_execution, Qt.ConnectionType.QueuedConnection)
        self.render_plan(plan)

    def render_plan(self, plan: OperationPlanViewState) -> None:
        self._rendering = True
        try:
            self._plan = plan
            self._preflight = None
            self._review_rows = ()
            self._review_needs_refresh = False
            self._check_ready = False
            self._retry_available = False
            self._needs_comparison = True
            self.setWindowTitle(plan.title)
            self._headline.setText(plan.title)
            scope = plan.scope_summary
            if plan.kind is OperationKind.DOWNLOAD:
                scope += "\n只更新当前工程的译文和翻译状态，不会写回 ESM/ESP。写回游戏文件需另行执行。"
            self._scope.setText(scope)
            fields = {field.field_id: field for field in plan.editable_fields}
            decisions = fields.get(sync_review_direction(plan.kind).decisions_field)
            self._sync_decisions = json.loads(decisions.value) if decisions is not None else {}
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
            self._impact.setText("开始对照后显示预计变化")
            self._set_status("先选择云端项目和同步方式，再点击“开始对照”。对照不会修改内容。", SemanticState.INFO)
        finally:
            self._rendering = False
        self._sync_controls()

    def render_preflight(self, result: OperationPreflightResult) -> None:
        if result.request_digest != self._plan.request_digest:
            return
        self._preflight = result
        self._needs_comparison = False
        self._review_rows = result.sync_review
        self._review_needs_refresh = False
        self._sync_decisions = {
            row.row_id: row.decision
            for row in self._review_rows
            if row.decision == sync_review_direction(self._plan.kind).keep_choice
            or (
                row.can_select_remote
                and any(candidate.candidate_id == row.decision and candidate.selectable for candidate in row.candidates)
            )
        }
        presentation = present_sync_preflight(result)
        self._impact.setText(presentation.impact)
        self._set_status(presentation.status, presentation.semantic)
        self._check_ready = presentation.can_confirm
        self._retry_available = presentation.can_retry
        self._sync_controls()

    def render_preflight_error(self, message: str) -> None:
        self._preflight = None
        self._check_ready = False
        self._retry_available = True
        self._needs_comparison = False
        self.set_preflight_running(False)
        if "changed while preflight" in message or "PREFLIGHT_STALE" in message:
            self._set_status("选项已变化，请按最新设置重新对照。", SemanticState.INFO)
            return
        self._set_status(str(message) or "检查失败，请稍后重试。", SemanticState.ERROR)

    def set_preflight_running(self, running: bool) -> None:
        self._preflight_running = running
        if running:
            self._needs_comparison = False
            self._preflight = None
            self._check_ready = False
            self._retry_available = False
            self._set_status(
                "正在准备本轮下载内容并对照，尚未修改工程。可关闭窗口退出；重新打开窗口可获取最新云端内容。"
                if self._plan.kind is OperationKind.DOWNLOAD
                else "正在读取云端内容并对照，尚未修改任何内容。可关闭窗口退出本次对照。",
                SemanticState.INFO,
            )
            self._progress.setRange(0, 0)
        self._progress.setVisible(running or self._execution_running)
        self._sync_controls()

    def set_execution_running(self, running: bool) -> None:
        self._execution_running = running
        self._check_ready = False
        self._retry_available = False
        self._preflight = None
        if running:
            action = "更新工程译文" if self._plan.kind is OperationKind.DOWNLOAD else "上传到 ParaTranz"
            self._set_status(f"正在{action}，请稍候…", SemanticState.INFO)
            self._progress.setRange(0, 0)
        self._progress.setVisible(running or self._preflight_running)
        self._sync_controls()

    def render_execution(self, activity: TaskActivityViewState, result: OperationResult | None = None) -> None:
        if not self._execution_running or not activity.is_terminal:
            return
        self.set_execution_running(False)
        self._retry_available = True
        self._cancel.setText("关闭")
        action = "更新工程译文" if self._plan.kind is OperationKind.DOWNLOAD else "上传到 ParaTranz"
        receipt = sync_receipt(activity, result, action=action, kind=self._plan.kind)
        semantic = {
            "completed": SemanticState.SUCCESS,
            "failed": SemanticState.ERROR,
        }.get(receipt.status, SemanticState.WARNING)
        self._set_status(receipt.text, semantic)
        self._status.setToolTip(receipt.details)
        verified = (
            receipt.status == "completed"
            and result is not None
            and not (result.counts.failed or result.counts.cancelled)
        )
        if verified:
            if self._plan.kind is OperationKind.DOWNLOAD and bool(
                getattr(self._context, "uses_authoritative_projection", False)
            ):
                self._set_status(receipt.text + "\n工程译文已自动保存到当前本地版本。", semantic)
            self._review_rows = tuple(
                row
                for row in self._review_rows
                if not row.decision or row.decision == sync_review_direction(self._plan.kind).keep_choice
            )
            remaining_ids = {row.row_id for row in self._review_rows}
            self._sync_decisions = {
                row_id: decision for row_id, decision in self._sync_decisions.items() if row_id in remaining_ids
            }
        self._review_needs_refresh = not verified
        if receipt.status == "completed":
            self._progress.setRange(0, 100)
            self._progress.setValue(100)
            self._progress.show()
        self._sync_controls()
        self.execution_finished.emit()

    def _sync_controls(self) -> None:
        busy = self._preflight_running or self._execution_running
        fields = {field.field_id: field for field in self._plan.editable_fields}
        for widget, field_id in (
            (self._choose_project, "paratranz_project_id"),
            (self._set_default, "set_as_default"),
            (self._apply_deletions, "apply_remote_deletions"),
            *((button, "conflict_policy") for button in self._strategy_buttons.values()),
        ):
            field = fields.get(field_id)
            widget.setEnabled(not busy and field is not None and field.enabled)
        self._cancel.setEnabled(not self._execution_running)
        self._review.setVisible(bool(self._review_rows))
        self._review.setEnabled(not busy and bool(self._review_rows))
        review_count = len(self._review_rows)
        self._review.setText(
            f"刷新这 {review_count:,} 条翻译…" if self._review_needs_refresh else f"确认 {review_count:,} 条翻译…"
        )
        self._confirm.setEnabled(not busy and (self._needs_comparison or self._check_ready or self._retry_available))
        action = self._action_text(self._plan.kind)
        if any(not row.decision for row in self._review_rows):
            action = sync_review_direction(self._plan.kind).safe_action
        self._confirm.setText("开始对照" if self._needs_comparison else "重新对照" if self._retry_available else action)

    def done(self, result: int) -> None:
        if not self._execution_running:
            super().done(result)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._execution_running:
            event.ignore()
        else:
            super().closeEvent(event)

    def edited_values(self) -> tuple[tuple[str, str], ...]:
        selected = self._strategy_group.checkedButton()
        policy = "" if selected is None else str(selected.property("policyValue") or "")
        values = (
            ("paratranz_project_id", self._project_id),
            ("paratranz_project_name", self._project_name),
            ("set_as_default", "true" if self._set_default.isChecked() else "false"),
            ("conflict_policy", policy),
            ("apply_remote_deletions", "true" if self._apply_deletions.isChecked() else "false"),
        )
        return values + (
            (sync_review_direction(self._plan.kind).decisions_field, json.dumps(self._sync_decisions, sort_keys=True)),
        )

    def _review_items(self) -> None:
        if self._preflight_running or self._execution_running or not self._review_rows:
            return
        if self._review_needs_refresh:
            self._request_preflight()
            return
        dialog = SyncReviewDialog(self._review_rows, self, kind=self._plan.kind)
        if dialog.exec():
            self._sync_decisions = dialog.decisions()
            self._controls_changed()
            self._request_preflight()
        dialog.deleteLater()

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
        if self._rendering or self._preflight_running or self._execution_running:
            return
        self._preflight = None
        self._check_ready = False
        self._retry_available = False
        self._needs_comparison = True
        self._review_rows = ()
        self._impact.setText("设置已改变，开始对照后显示预计变化")
        self._set_status("设置已改变，请点击“开始对照”核对这次的变化。", SemanticState.INFO)
        self.plan_input_changed.emit()
        self._sync_controls()

    def _request_preflight(self) -> None:
        if self._preflight_running or self._execution_running:
            return
        self.set_preflight_running(True)
        self.preflight_requested.emit(self._plan.session_id, self.edited_values())

    def _confirm_operation(self) -> None:
        if self._preflight_running or self._execution_running:
            return
        if self._needs_comparison or self._retry_available:
            self._request_preflight()
            return
        token = None if self._preflight is None else self._preflight.confirmation_token
        if token is not None:
            self.confirm_requested.emit(self._plan.session_id, token)

    def _choose_remote_project(self) -> None:
        if self._preflight_running or self._execution_running:
            return
        config_revision = int(getattr(getattr(self._context, "config", None), "config_revision", 0))
        dialog = ParaTranzTargetDialog(
            self._context,
            self._catalog,
            config_revision,
            self,
            explanation="选择这次要上传或下载翻译的 ParaTranz 项目。",
            confirmation_text="使用这个项目",
        )
        if not dialog.exec():
            return
        project = dialog.selected_project()
        if project is None:
            return
        self._project_id = str(project["id"])
        self._project_name = str(project["name"])
        self._sync_decisions = {}
        self._target_name.setText(self._project_name)
        self._target_detail.setText("本次选择")
        self._set_default.setVisible(getattr(self._context, "active_project_id", None) is not None)
        self._set_default.setEnabled(self._set_default.isVisible())
        self._controls_changed()

    def _set_status(self, text: str, state: SemanticState) -> None:
        self._status.setText(text)
        self._status.setToolTip("")
        update_accessible_state(self._status, text)
        ComponentStyle.apply_state(self._status, state)

    @staticmethod
    def _target_detail_text(target: str) -> str:
        if "当前工程已绑定" in target:
            return "当前工程默认使用的项目"
        if "本次选择" in target:
            return "本次选择"
        return "尚未设置默认项目"

    @staticmethod
    def _action_text(kind: OperationKind) -> str:
        return "更新工程译文" if kind is OperationKind.DOWNLOAD else "上传到 ParaTranz"


__all__ = ["ParaTranzSyncDialog"]
