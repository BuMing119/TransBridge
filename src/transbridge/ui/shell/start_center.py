"""Start-center state and the thin Qt view that renders it.

The immutable state is intentionally independent from repository and task
implementations.  The widget only emits stable user intents; callers own file
dialogs, application commands, and navigation side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.accessibility import configure_accessible_widget, update_accessible_state
from transbridge.ui.foundation.components import (
    ComponentDensity,
    ComponentKind,
    ComponentStyle,
    SemanticState,
    make_primary_button,
)
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .start_center_landing import StartCenterLanding


def _configure_heading(label: QLabel, *, point_size: float, accessible_name: str) -> QLabel:
    """Use scalable Qt font metrics while leaving all colour roles to QPalette."""

    font = QFont(label.font())
    font.setPointSizeF(point_size)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    ComponentStyle.apply_static(label, ComponentKind.LABEL)
    configure_accessible_widget(label, name=accessible_name)
    return label


class StartDestinationState(StrEnum):
    RESTORING_LAST = "restoring-last"
    START_CENTER_EMPTY = "start-center-empty"
    START_CENTER_RECOVERY_FAILED = "start-center-recovery-failed"
    START_CENTER_USER_REQUESTED = "start-center-user-requested"


@dataclass(frozen=True, slots=True)
class RecentProjectViewState:
    project_key: str
    name: str
    path: str
    available: bool
    reason: str = ""
    active: bool = False

    def __post_init__(self) -> None:
        if not self.project_key.strip() or not self.name.strip():
            raise ValueError("recent project identity and name must not be empty")
        if not self.available and not self.reason.strip():
            raise ValueError("an unavailable recent project requires a reason")


@dataclass(frozen=True, slots=True)
class RecoveryItemViewState:
    storage_key: str
    title: str
    recoverable: bool
    reason: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.storage_key.strip() or not self.title.strip():
            raise ValueError("recovery identity and title must not be empty")
        if not self.recoverable and not self.reason.strip():
            raise ValueError("an unavailable recovery item requires a reason")


@dataclass(frozen=True, slots=True)
class StartCenterViewState:
    destination: StartDestinationState
    revision: int
    recent_projects: tuple[RecentProjectViewState, ...] = ()
    recovery_items: tuple[RecoveryItemViewState, ...] = ()
    active_project_name: str | None = None
    dirty: bool = False
    diagnostic_code: str = ""
    diagnostic_message: str = ""
    recovery_diagnostic_message: str = ""

    def __post_init__(self) -> None:
        if self.revision < 0:
            raise ValueError("start-center revision must not be negative")
        if self.destination is StartDestinationState.START_CENTER_RECOVERY_FAILED:
            if not self.diagnostic_code.strip() or not self.diagnostic_message.strip():
                raise ValueError("restore failure requires a stable diagnostic")


class StartCenterWidget(QWidget):
    """Render start/draft projections without accessing disk or services."""

    choose_plugin_requested = pyqtSignal()
    open_project_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)
    open_recent_in_new_window_requested = pyqtSignal(str)
    recovery_details_requested = pyqtSignal(str)
    task_center_requested = pyqtSignal()
    create_empty_requested = pyqtSignal()
    open_fomod_requested = pyqtSignal()
    return_to_current_requested = pyqtSignal()
    project_name_changed = pyqtSignal(str)
    variant_name_changed = pyqtSignal(str)
    skip_empty_changed = pyqtSignal(bool)
    choose_migration_requested = pyqtSignal()
    prepare_requested = pyqtSignal()
    commit_requested = pyqtSignal()
    return_to_landing_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        configure_accessible_widget(self, name="开始中心", description="选择或恢复本地翻译工程")
        self._state_revision = -1
        self._draft_revision = -1
        self._pages = QStackedWidget(self)
        self._landing_page = self._build_landing_page()
        self._draft_page = self._build_draft_page()
        self._pages.addWidget(self._landing_page)
        self._pages.addWidget(self._draft_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._pages)

    def _build_landing_page(self) -> QWidget:
        page = StartCenterLanding(self)
        page.choose_plugin_requested.connect(self.choose_plugin_requested)
        page.open_project_requested.connect(self.open_project_requested)
        page.open_recent_requested.connect(self.open_recent_requested)
        page.open_recent_in_new_window_requested.connect(self.open_recent_in_new_window_requested)
        page.create_empty_requested.connect(self.create_empty_requested)
        page.open_fomod_requested.connect(self.open_fomod_requested)
        page.return_to_current_requested.connect(self.return_to_current_requested)
        page.task_center_requested.connect(self.task_center_requested)

        # Compatibility names retained for existing coordinators and focused
        # UI contracts while the landing page owns their visual composition.
        self.choose_plugin_button = page.choose_plugin_button
        self._open_button = page._open_button
        self._empty_button = page._empty_button
        self._fomod_button = page._fomod_button
        self._status_label = page._status_label
        self._return_button = page._return_button
        self._recent_list = page._project_list
        self._project_list = page._project_list
        self._projects_empty = page._projects_empty
        self._recovery_banner = page._recovery_banner
        self._task_center_button = page._task_center_button
        self._content = page._content
        self._task_panel = page._task_panel
        self._projects_panel = page._projects_panel
        self._project_open_progress = page._projects_panel.progress_container
        self._project_open_progress_bar = page._projects_panel.progress_bar
        return page

    def set_creation_available(self, available: bool, reason: str = "") -> None:
        """Apply the guided-project capability without losing it on rerender."""

        self._landing_page.set_creation_available(available, reason)

    def set_project_opening(self, message: str | None) -> None:
        """Show or clear start-center feedback for a current-window switch."""

        self._landing_page.set_project_opening(message)

    def _build_draft_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 32)
        layout.setSpacing(20)

        header = QHBoxLayout()
        header.setSpacing(12)
        self._draft_back = QPushButton(page)
        self._draft_back.setObjectName("startCenterDraftBack")
        self._draft_back.setIcon(tabler_icon(self._draft_back, "arrow-left", 20))
        self._draft_back.setIconSize(QSize(20, 20))
        self._draft_back.setFixedSize(40, 40)
        self._draft_back.setFlat(True)
        self._draft_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._draft_back.setToolTip("返回开始")
        ComponentStyle.apply_static(self._draft_back, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        configure_accessible_widget(self._draft_back, name="返回开始中心", description="返回开始页面")
        self._draft_back.clicked.connect(self.return_to_landing_requested)
        header.addWidget(self._draft_back)
        title = QLabel("创建本地翻译工程", page)
        _configure_heading(title, point_size=16.0, accessible_name="创建本地翻译工程")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)
        form = QFormLayout()
        self._source_label = QLabel(page)
        self._name_edit = QLineEdit(page)
        self._name_edit.setAccessibleName("本地翻译工程名称")
        ComponentStyle.apply_static(self._name_edit, ComponentKind.INPUT)
        self._name_edit.textEdited.connect(self.project_name_changed)
        self._name_edit.returnPressed.connect(self._activate_draft_primary)
        self._variant_edit = QLineEdit(page)
        self._variant_edit.setAccessibleName("默认翻译版本名称")
        ComponentStyle.apply_static(self._variant_edit, ComponentKind.INPUT)
        self._variant_edit.textEdited.connect(self.variant_name_changed)
        self._variant_edit.returnPressed.connect(self._activate_draft_primary)
        self._migration_label = QLabel("不导入", page)
        self._migration_label.setAccessibleName("已有译文来源")
        self._migration_button = QPushButton("选择已有译文", page)
        self._migration_button.setAccessibleName("选择已有译文来源")
        self._migration_button.clicked.connect(self.choose_migration_requested)
        migration_row = QHBoxLayout()
        migration_row.addWidget(self._migration_label, 1)
        migration_row.addWidget(self._migration_button)
        form.addRow("插件", self._source_label)
        form.addRow("工程名称", self._name_edit)
        form.addRow("默认翻译版本", self._variant_edit)
        form.addRow("已有译文", migration_row)
        layout.addLayout(form)

        advanced = QGroupBox("高级解析设置", page)
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_layout = QVBoxLayout(advanced)
        self._skip_empty = QCheckBox("忽略空源文本", advanced)
        self._skip_empty.setAccessibleName("忽略空的源文本")
        self._skip_empty.toggled.connect(self.skip_empty_changed)
        advanced_layout.addWidget(self._skip_empty)
        layout.addWidget(advanced)

        self._draft_summary = QLabel(page)
        self._draft_summary.setWordWrap(True)
        self._draft_summary.setAccessibleName("工程创建方案摘要")
        ComponentStyle.apply_static(self._draft_summary, ComponentKind.NOTIFICATION)
        layout.addWidget(self._draft_summary)
        self._draft_error = QLabel(page)
        self._draft_error.setWordWrap(True)
        self._draft_error.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._draft_error.setAccessibleName("工程创建错误")
        ComponentStyle.apply_static(self._draft_error, ComponentKind.NOTIFICATION)
        layout.addWidget(self._draft_error)

        actions = QHBoxLayout()
        self._draft_primary = make_primary_button("", page)
        self._draft_primary.setAccessibleName("确定创建本地翻译工程并开始翻译")
        self._draft_primary.clicked.connect(self._emit_draft_primary)
        actions.addStretch(1)
        actions.addWidget(self._draft_primary)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def render(self, state: StartCenterViewState) -> None:
        if state.revision < self._state_revision:
            return
        self._state_revision = state.revision
        self._pages.setCurrentWidget(self._landing_page)
        self._landing_page.render(state)

    def render_draft(self, state) -> None:
        """Render a GuidedProjectDraftState via its narrow public fields."""

        if state.revision < self._draft_revision:
            return
        self._draft_revision = state.revision
        self._pages.setCurrentWidget(self._draft_page)
        self._source_label.setText(state.source_path or "无源文件（空工程）")
        if self._name_edit.text() != state.project_name:
            self._name_edit.setText(state.project_name)
        if self._variant_edit.text() != state.default_variant_name:
            self._variant_edit.setText(state.default_variant_name)
        migrations = tuple(state.migration_sources)
        self._migration_label.setText("、".join(migrations) if migrations else "不导入")
        options = dict(state.parse_options)
        self._skip_empty.setChecked(bool(options.get("skip_empty", False)))
        self._draft_summary.setText(state.summary)
        diagnostic = ""
        if state.diagnostic_code:
            diagnostic = f"{state.diagnostic_code}: {state.diagnostic_message}"
        self._draft_error.setText(diagnostic)
        update_accessible_state(self._draft_error, diagnostic or "当前没有工程创建错误")
        ComponentStyle.apply_state(
            self._draft_error,
            SemanticState.ERROR if diagnostic else SemanticState.DEFAULT,
        )
        self._draft_primary.setText("确定")
        ComponentStyle.apply_state(self._draft_primary, SemanticState.PRIMARY)
        self._draft_primary.setEnabled(state.can_submit and not state.in_flight)
        self._name_edit.setEnabled(not state.in_flight)
        self._variant_edit.setEnabled(not state.in_flight)
        if diagnostic:
            self._draft_error.setFocus(Qt.FocusReason.OtherFocusReason)
        elif self._draft_primary.isEnabled():
            self._draft_primary.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._name_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _emit_draft_primary(self) -> None:
        self.prepare_requested.emit()

    def _activate_draft_primary(self) -> None:
        if self._pages.currentWidget() is self._draft_page and self._draft_primary.isEnabled():
            self._draft_primary.click()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape and self._pages.currentWidget() is self._draft_page:
            self.return_to_landing_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = [
    "RecentProjectViewState",
    "RecoveryItemViewState",
    "StartCenterViewState",
    "StartCenterWidget",
    "StartDestinationState",
]
