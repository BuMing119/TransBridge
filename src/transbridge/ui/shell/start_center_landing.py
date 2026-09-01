"""Task-oriented landing page for the start center.

This module deliberately consumes the start-center projection through a small
duck-typed surface.  Keeping the view-state types out of the import graph lets
``start_center.py`` compose this widget without creating a runtime cycle.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPalette, QResizeEvent
from PyQt6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.accessibility import configure_accessible_widget, update_accessible_state
from transbridge.ui.foundation.components import (
    ComponentDensity,
    ComponentKind,
    ComponentStyle,
    SemanticState,
    ThemedCard,
)
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .start_center_projects import StartCenterProjectsPanel

_RESTORING_LAST = "restoring-last"
_START_CENTER_EMPTY = "start-center-empty"
_START_CENTER_RECOVERY_FAILED = "start-center-recovery-failed"
_START_CENTER_USER_REQUESTED = "start-center-user-requested"
_WIDE_LAYOUT_BREAKPOINT = 1040
_CONTENT_MAXIMUM_WIDTH = 1400


def _destination_value(destination: object) -> str:
    """Normalize a StrEnum or plain string without importing its owner."""

    return str(getattr(destination, "value", destination))


def _heading(text: str, parent: QWidget, *, point_size: float) -> QLabel:
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setPointSizeF(point_size)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    ComponentStyle.apply_static(label, ComponentKind.LABEL)
    configure_accessible_widget(label, name=text)
    return label


class _TaskActionButton(QPushButton):
    """Large two-level action card that preserves native button semantics."""

    def __init__(
        self,
        title: str,
        description: str,
        icon_id: str,
        parent: QWidget,
        *,
        primary: bool = False,
    ) -> None:
        super().__init__("", parent)
        self._primary_labels: tuple[QLabel, ...] = ()
        ComponentStyle.apply_static(self, ComponentKind.BUTTON)
        if primary:
            ComponentStyle.apply_state(self, SemanticState.PRIMARY)
        self.setProperty("tbStartAction", True)
        self.setProperty("tbStartActionPrimary", primary)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(120 if primary else 112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 16, 12)
        layout.setSpacing(16)
        icon = QLabel(self)
        icon.setProperty("tbStartActionIcon", True)
        icon.setPixmap(
            tabler_icon(icon, icon_id, 30, semantic="on-accent" if primary else "navigation").pixmap(QSize(30, 30))
        )
        icon.setFixedSize(40, 40)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(icon)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(4)
        title_label = QLabel(title, self)
        title_label.setProperty("tbStartActionTitle", True)
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        description_label = QLabel(description, self)
        description_label.setProperty("tbStartActionDescription", True)
        if not primary:
            description_label.setForegroundRole(QPalette.ColorRole.Shadow)
        description_label.setWordWrap(True)
        description_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text_column.addWidget(title_label)
        text_column.addWidget(description_label)
        layout.addLayout(text_column, 1)

        arrow = QLabel(self)
        arrow.setProperty("tbStartActionArrow", True)
        if not primary:
            arrow.setForegroundRole(QPalette.ColorRole.Shadow)
        arrow.setPixmap(tabler_icon(arrow, "chevron-right", 22).pixmap(QSize(22, 22)))
        arrow.setFixedSize(32, 32)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(arrow)
        self._primary_labels = (title_label, description_label, arrow) if primary else ()
        self._refresh_primary_foreground()
        if primary:
            QTimer.singleShot(0, self._refresh_primary_foreground)
        configure_accessible_widget(self, name=title, description=description)

    def _refresh_primary_foreground(self) -> None:
        if not self._primary_labels:
            return
        foreground = QApplication.palette().color(QPalette.ColorRole.Button)
        for label in self._primary_labels:
            palette = QPalette(label.palette())
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                palette.setColor(group, QPalette.ColorRole.WindowText, foreground)
                palette.setColor(group, QPalette.ColorRole.Text, foreground)
            label.setPalette(palette)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.StyleChange,
        }:
            QTimer.singleShot(0, self._refresh_primary_foreground)


def _task_button(
    text: str,
    description: str,
    icon_id: str,
    parent: QWidget,
    *,
    primary: bool = False,
) -> QPushButton:
    return _TaskActionButton(text, description, icon_id, parent, primary=primary)


class StartCenterLanding(QWidget):
    """Render the task launcher, local catalog, and recovery summary."""

    choose_plugin_requested = pyqtSignal()
    open_project_requested = pyqtSignal()
    open_recent_requested = pyqtSignal(str)
    open_recent_in_new_window_requested = pyqtSignal(str)
    create_empty_requested = pyqtSignal()
    open_fomod_requested = pyqtSignal()
    return_to_current_requested = pyqtSignal()
    task_center_requested = pyqtSignal()
    delete_project_requested = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        configure_accessible_widget(self, name="开始中心", description="创建翻译任务或打开本地工程")
        self._state_revision = -1
        self._is_compact: bool | None = None
        self._creation_available = True
        self._creation_unavailable_reason = ""
        self._project_opening = False

        root = QHBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 28)
        root.addStretch(1)

        self._content = QWidget(self)
        self._content.setMaximumWidth(_CONTENT_MAXIMUM_WIDTH)
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(30)

        title = _heading("开始", self._content, point_size=20.0)
        title.setObjectName("startCenterTitle")
        content_layout.addWidget(title)
        subtitle = QLabel("创建新的翻译任务，或继续已有本地工程。", self._content)
        subtitle.setObjectName("startCenterSubtitle")
        subtitle.setWordWrap(True)
        ComponentStyle.apply_static(subtitle, ComponentKind.LABEL)
        content_layout.addWidget(subtitle)

        self._status_banner = ThemedCard(self._content)
        status_layout = QHBoxLayout(self._status_banner)
        status_layout.setContentsMargins(16, 8, 16, 8)
        status_layout.setSpacing(8)
        self._status_label = QLabel(self._status_banner)
        self._status_label.setWordWrap(True)
        self._status_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        ComponentStyle.apply_static(self._status_label, ComponentKind.LABEL, ComponentDensity.COMPACT)
        configure_accessible_widget(self._status_label, name="开始中心状态")
        status_layout.addWidget(self._status_label, 1)
        self._return_button = QPushButton("返回当前工程", self._status_banner)
        ComponentStyle.apply_static(self._return_button, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        configure_accessible_widget(self._return_button, name="返回当前本地翻译工程")
        self._return_button.clicked.connect(self.return_to_current_requested)
        status_layout.addWidget(self._return_button)
        content_layout.addWidget(self._status_banner)

        self._body_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(32)
        self._task_panel = self._build_task_panel()
        self._projects_panel = StartCenterProjectsPanel(self._content)
        self._projects_panel.open_current_requested.connect(self.open_recent_requested)
        self._projects_panel.open_new_window_requested.connect(self.open_recent_in_new_window_requested)
        self._projects_panel.return_to_current_requested.connect(self.return_to_current_requested)
        self._projects_panel.delete_requested.connect(self.delete_project_requested)
        self._project_list = self._projects_panel.project_list
        self._projects_empty = self._projects_panel.projects_empty
        self._project_count = self._projects_panel.project_count
        self._body_layout.addWidget(self._task_panel)
        self._body_layout.addWidget(self._projects_panel)
        self._body_layout.setStretch(0, 0)
        self._body_layout.setStretch(1, 1)
        content_layout.addLayout(self._body_layout, 1)

        self._recovery_banner = ThemedCard(self._content)
        self._recovery_banner.setMinimumHeight(64)
        recovery_layout = QHBoxLayout(self._recovery_banner)
        recovery_layout.setContentsMargins(16, 10, 16, 10)
        recovery_layout.setSpacing(12)
        recovery_icon = QLabel(self._recovery_banner)
        recovery_icon.setPixmap(tabler_icon(recovery_icon, "info-circle", 20, semantic="accent").pixmap(QSize(20, 20)))
        recovery_icon.setAccessibleName("可继续任务")
        recovery_layout.addWidget(recovery_icon)
        self._recovery_label = QLabel(self._recovery_banner)
        ComponentStyle.apply_static(self._recovery_label, ComponentKind.LABEL)
        recovery_layout.addWidget(self._recovery_label, 1)
        self._task_center_button = QPushButton("打开任务中心", self._recovery_banner)
        ComponentStyle.apply_static(self._task_center_button, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        configure_accessible_widget(self._task_center_button, name="打开任务中心")
        self._task_center_button.clicked.connect(self.task_center_requested)
        recovery_layout.addWidget(self._task_center_button)
        ComponentStyle.apply_state(self._recovery_banner, SemanticState.INFO)
        content_layout.addWidget(self._recovery_banner)

        content_layout.addStretch(1)
        # Give the content priority over the symmetric centering spacers; its
        # maximum width then becomes the wide-screen cap without imposing a
        # minimum width that would prevent the compact layout from activating.
        root.addWidget(self._content, 100, Qt.AlignmentFlag.AlignTop)
        root.addStretch(1)

        self._recovery_banner.hide()
        self._return_button.hide()
        self._set_compact_layout(False)

    def _build_task_panel(self) -> ThemedCard:
        panel = ThemedCard(self._content)
        panel.setObjectName("startCenterTaskPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(22, 24, 22, 24)
        layout.setSpacing(20)
        heading = _heading("开始新任务", panel, point_size=16.0)
        heading.setProperty("tbStartPanelHeading", True)
        layout.addWidget(heading)

        self.choose_plugin_button = _task_button(
            "选择插件",
            "选择 ESP、ESM 或 ESL，自动准备工程和可编辑内容",
            "language",
            panel,
            primary=True,
        )
        self.choose_plugin_button.setObjectName("startCenterPrimaryAction")
        self.choose_plugin_button.clicked.connect(self.choose_plugin_requested)
        layout.addWidget(self.choose_plugin_button)

        self._fomod_button = _task_button(
            "翻译 FOMOD 安装包",
            "翻译安装向导文本并生成新的安装包",
            "package",
            panel,
        )
        self._fomod_button.clicked.connect(self.open_fomod_requested)
        layout.addWidget(self._fomod_button)

        self._open_button = _task_button(
            "打开 TransBridge 工程",
            "打开保存在本机的 TransBridge 工程",
            "folder",
            panel,
        )
        self._open_button.clicked.connect(self.open_project_requested)
        layout.addWidget(self._open_button)

        self._empty_button = QPushButton("高级：创建空工程（不导入插件）", panel)
        self._empty_button.setFlat(True)
        self._empty_button.setProperty("tbStartAdvanced", True)
        self._empty_button.setIcon(tabler_icon(self._empty_button, "plus", 18, semantic="accent"))
        self._empty_button.setIconSize(QSize(18, 18))
        ComponentStyle.apply_static(self._empty_button, ComponentKind.BUTTON, ComponentDensity.COMPACT)
        configure_accessible_widget(self._empty_button, name="创建不导入插件的空工程")
        self._empty_button.clicked.connect(self.create_empty_requested)
        layout.addWidget(self._empty_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return panel

    def render(self, state: Any) -> None:
        """Render a duck-typed StartCenterViewState projection."""

        revision = int(getattr(state, "revision", 0))
        if revision < self._state_revision:
            return
        self._state_revision = revision
        destination = _destination_value(getattr(state, "destination", _START_CENTER_EMPTY))
        self._render_status(state, destination)
        self._projects_panel.render(tuple(getattr(state, "recent_projects", ())))
        self._render_recovery(tuple(getattr(state, "recovery_items", ())))

        restoring = destination == _RESTORING_LAST
        creation_enabled = self._creation_available and not restoring and not self._project_opening
        self.choose_plugin_button.setEnabled(creation_enabled)
        self._open_button.setEnabled(not restoring and not self._project_opening)
        self._empty_button.setEnabled(creation_enabled)
        self._fomod_button.setEnabled(not restoring and not self._project_opening)
        self._projects_panel.set_interaction_enabled(not restoring)
        if self.choose_plugin_button.isEnabled():
            self.choose_plugin_button.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._status_label.setFocus(Qt.FocusReason.OtherFocusReason)

    def _render_status(self, state: object, destination: str) -> None:
        if destination == _RESTORING_LAST:
            message = "正在恢复上次工程…恢复完成前暂不能切换工程。"
            semantic = SemanticState.INFO
        elif destination == _START_CENTER_RECOVERY_FAILED:
            code = str(getattr(state, "diagnostic_code", "")).strip()
            detail = str(getattr(state, "diagnostic_message", "")).strip()
            message = f"{code}: {detail}" if code else (detail or "恢复上次工程失败，可打开其他工程继续。")
            semantic = SemanticState.ERROR
        elif destination == _START_CENTER_USER_REQUESTED:
            project_name = str(getattr(state, "active_project_name", "") or "当前工程")
            dirty_suffix = "，有未保存修改" if bool(getattr(state, "dirty", False)) else ""
            message = f"工程“{project_name}”仍保持打开{dirty_suffix}。"
            semantic = SemanticState.WARNING if dirty_suffix else SemanticState.INFO
        else:
            recovery_diagnostic = str(getattr(state, "recovery_diagnostic_message", "")).strip()
            message = recovery_diagnostic or "选择一种任务开始，或从本地工程继续工作。"
            semantic = SemanticState.WARNING if recovery_diagnostic else SemanticState.INFO

        self._status_label.setText(message)
        self._status_label.setProperty("tbStatusId", destination)
        ComponentStyle.apply_state(self._status_label, semantic)
        ComponentStyle.apply_state(self._status_banner, semantic)
        update_accessible_state(self._status_label, message)
        show_return = destination == _START_CENTER_USER_REQUESTED and bool(getattr(state, "active_project_name", None))
        self._return_button.setVisible(show_return)
        show_status = destination in {_RESTORING_LAST, _START_CENTER_RECOVERY_FAILED}
        show_status = show_status or bool(str(getattr(state, "recovery_diagnostic_message", "")).strip())
        show_status = show_status or (
            destination == _START_CENTER_USER_REQUESTED and bool(getattr(state, "dirty", False))
        )
        self._status_banner.setVisible(show_status)

    def set_creation_available(self, available: bool, reason: str = "") -> None:
        """Persist guided-creation capability across landing rerenders."""

        self._creation_available = available
        self._creation_unavailable_reason = reason.strip()
        self.choose_plugin_button.setEnabled(available and not self._project_opening)
        self._empty_button.setEnabled(available and not self._project_opening)
        self.choose_plugin_button.setToolTip("" if available else self._creation_unavailable_reason)
        self._empty_button.setToolTip("" if available else self._creation_unavailable_reason)
        if not available and self.choose_plugin_button.hasFocus():
            self._open_button.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_project_opening(self, message: str | None) -> None:
        """Keep project-opening feedback visible while the worker is active."""

        self._project_opening = message is not None
        self._projects_panel.set_opening(message)
        enabled = not self._project_opening
        self.choose_plugin_button.setEnabled(self._creation_available and enabled)
        self._empty_button.setEnabled(self._creation_available and enabled)
        self._open_button.setEnabled(enabled)
        self._fomod_button.setEnabled(enabled)

    def _render_recovery(self, recovery_items: tuple[object, ...]) -> None:
        recoverable_count = sum(bool(getattr(item, "recoverable", False)) for item in recovery_items)
        if recoverable_count:
            message = f"有 {recoverable_count} 个任务可以继续"
            self._recovery_label.setText(message)
            update_accessible_state(self._recovery_label, message)
            self._recovery_banner.show()
        else:
            self._recovery_label.clear()
            self._recovery_banner.hide()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._set_compact_layout(event.size().width() < _WIDE_LAYOUT_BREAKPOINT)

    def _set_compact_layout(self, compact: bool) -> None:
        if compact == self._is_compact and self._body_layout.direction() == (
            QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        ):
            return
        self._is_compact = compact
        direction = QBoxLayout.Direction.TopToBottom if compact else QBoxLayout.Direction.LeftToRight
        self._body_layout.setDirection(direction)
        self._task_panel.setMaximumWidth(_CONTENT_MAXIMUM_WIDTH if compact else 520)
        self._task_panel.setMinimumWidth(0 if compact else 520)
        panel_minimum_height = 0 if compact else 600
        self._task_panel.setMinimumHeight(panel_minimum_height)
        self._projects_panel.setMinimumHeight(panel_minimum_height)
        self._body_layout.setStretch(0, 0)
        self._body_layout.setStretch(1, 1)


__all__ = ["StartCenterLanding"]
