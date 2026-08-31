"""Local-project panel for the task-oriented start center."""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.accessibility import configure_accessible_widget, update_accessible_state
from transbridge.ui.foundation.components import (
    ComponentDensity,
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    StatusBadge,
    ThemedCard,
)
from transbridge.ui.foundation.tabler_icons import tabler_icon

from .project_open_choice_dialog import ProjectOpenChoiceDialog

PROJECT_PATH_ROLE = int(Qt.ItemDataRole.UserRole)
PROJECT_ACTIVE_ROLE = PROJECT_PATH_ROLE + 1
PROJECT_NAME_ROLE = PROJECT_PATH_ROLE + 2


def _heading(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    font = QFont(label.font())
    font.setPointSizeF(16.0)
    font.setWeight(QFont.Weight.DemiBold)
    label.setFont(font)
    label.setProperty("tbStartPanelHeading", True)
    ComponentStyle.apply_static(label, ComponentKind.LABEL)
    configure_accessible_widget(label, name=text)
    return label


class _ProjectRow(QWidget):
    """Visual companion for a QListWidgetItem; the item owns interaction."""

    def __init__(self, project: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        name = str(getattr(project, "name", ""))
        path = str(getattr(project, "path", ""))
        available = bool(getattr(project, "available", False))
        active = bool(getattr(project, "active", False))
        reason = str(getattr(project, "reason", "")).strip()
        self.setProperty("tbProjectActive", active)
        self.setProperty("tbProjectAvailable", available)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(16)
        icon = QLabel(self)
        icon_id = "alert-triangle" if not available else "folder"
        semantic = "accent" if active else "navigation"
        icon.setPixmap(tabler_icon(icon, icon_id, 30, semantic=semantic).pixmap(QSize(30, 30)))
        icon.setFixedSize(40, 40)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setAccessibleName("不可用工程" if not available else ("当前工程" if active else "本地工程"))
        layout.addWidget(icon, alignment=Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        name_label = ElidedLabel(name, self)
        ComponentStyle.apply_static(name_label, ComponentKind.LABEL)
        name_label.setProperty("tbStartProjectName", True)
        text_column.addWidget(name_label)
        if path:
            path_label = ElidedLabel(path, self)
            ComponentStyle.apply_static(path_label, ComponentKind.LABEL, ComponentDensity.COMPACT)
            path_label.setProperty("tbSecondaryText", True)
            path_label.setProperty("tbStartProjectPath", True)
            text_column.addWidget(path_label)
        layout.addLayout(text_column, 1)

        if not available:
            layout.addWidget(StatusBadge(reason or "工程不可用", SemanticState.WARNING, self))
        elif active:
            layout.addWidget(StatusBadge("正在使用", SemanticState.INFO, self))
        if available:
            arrow = QLabel(self)
            arrow.setProperty("tbStartProjectArrow", True)
            arrow.setPixmap(tabler_icon(arrow, "chevron-right", 22).pixmap(QSize(22, 22)))
            arrow.setFixedSize(32, 32)
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(arrow)

        description = "；".join(part for part in (path, reason) if part)
        configure_accessible_widget(self, name=name, description=description or "可打开的本地工程")


class _ProjectList(QListWidget):
    """Invoke project rows consistently with one mouse click or Enter."""

    project_invoked = pyqtSignal(object)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        item = self.itemAt(event.position().toPoint())
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and item is not None:
            self.project_invoked.emit(item)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in {Qt.Key.Key_Enter, Qt.Key.Key_Return}:
            item = self.currentItem()
            if item is not None:
                self.project_invoked.emit(item)
                event.accept()
                return
        super().keyPressEvent(event)


class StartCenterProjectsPanel(ThemedCard):
    """Render local projects and expose explicit window-opening choices."""

    open_current_requested = pyqtSignal(str)
    open_new_window_requested = pyqtSignal(str)
    return_to_current_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("startCenterProjectsPanel")
        self._interaction_enabled = True
        self._opening = False
        self._project_dialog: ProjectOpenChoiceDialog | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 24)
        layout.setSpacing(20)
        header = QHBoxLayout()
        header.setContentsMargins(28, 0, 28, 0)
        header.addWidget(_heading("本地工程", self))
        header.addStretch(1)
        self.project_count = QLabel(self)
        ComponentStyle.apply_static(self.project_count, ComponentKind.LABEL, ComponentDensity.COMPACT)
        header.addWidget(self.project_count)
        layout.addLayout(header)

        self.progress_container = QWidget(self)
        progress_layout = QVBoxLayout(self.progress_container)
        progress_layout.setContentsMargins(28, 0, 28, 0)
        progress_layout.setSpacing(8)
        self.progress_label = QLabel(self.progress_container)
        ComponentStyle.apply_static(self.progress_label, ComponentKind.LABEL, ComponentDensity.COMPACT)
        configure_accessible_widget(self.progress_label, name="工程打开进度")
        progress_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar(self.progress_container)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setAccessibleName("正在打开本地工程")
        ComponentStyle.apply_static(self.progress_bar, ComponentKind.PROGRESS)
        self.progress_bar.setFixedHeight(4)
        progress_layout.addWidget(self.progress_bar)
        self.progress_container.hide()
        layout.addWidget(self.progress_container)

        self.project_list = _ProjectList(self)
        self.project_list.setObjectName("startCenterProjectList")
        self.project_list.setAccessibleName("本地工程")
        self.project_list.setUniformItemSizes(True)
        self.project_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.project_list.setMinimumHeight(400)
        ComponentStyle.apply_static(self.project_list, ComponentKind.TABLE)
        self.project_list.project_invoked.connect(self._activate_project)
        layout.addWidget(self.project_list, 1)

        self.projects_empty = QWidget(self)
        self.projects_empty.setProperty("tbStartEmptyState", True)
        ComponentStyle.apply_static(self.projects_empty, ComponentKind.EMPTY_STATE)
        configure_accessible_widget(
            self.projects_empty,
            name="没有本地工程",
            description="选择插件、翻译 FOMOD 或打开 TransBridge 工程即可开始",
        )
        empty_layout = QVBoxLayout(self.projects_empty)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(10)
        empty_layout.addStretch(1)
        empty_icon = QLabel(self.projects_empty)
        empty_icon.setPixmap(tabler_icon(empty_icon, "folder", 46, semantic="accent").pixmap(QSize(46, 46)))
        empty_icon.setFixedSize(56, 56)
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_title = QLabel("尚无本地工程", self.projects_empty)
        empty_title.setProperty("tbStartEmptyTitle", True)
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_description = QLabel("从左侧创建翻译任务，或打开保存在本机的 TransBridge 工程。", self.projects_empty)
        empty_description.setProperty("tbStartEmptyDescription", True)
        empty_description.setWordWrap(True)
        empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_description)
        empty_layout.addStretch(1)
        layout.addWidget(self.projects_empty, 1)

    def render(self, projects: tuple[object, ...]) -> None:
        self.project_list.clear()
        for project in projects:
            name = str(getattr(project, "name", ""))
            path = str(getattr(project, "path", ""))
            available = bool(getattr(project, "available", False))
            active = bool(getattr(project, "active", False))
            reason = str(getattr(project, "reason", "")).strip()
            state_text = reason if not available else ("正在使用" if active else "可打开")
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{name}，{state_text}")
            item.setData(PROJECT_PATH_ROLE, path)
            item.setData(PROJECT_ACTIVE_ROLE, active)
            item.setData(PROJECT_NAME_ROLE, name)
            item.setSizeHint(QSize(0, 120 if path else 96))
            if not available:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(reason)
                item.setStatusTip(reason)
            else:
                item.setToolTip(f"返回 {name}" if active else f"选择 {name} 的打开方式")
            self.project_list.addItem(item)
            self.project_list.setItemWidget(item, _ProjectRow(project, self.project_list))

        count = len(projects)
        self.project_count.setText(f"共 {count} 个工程")
        self.project_count.setAccessibleName(f"本地工程共 {count} 个")
        self.project_list.setVisible(bool(projects))
        self.projects_empty.setVisible(not projects)
        self._apply_enabled_state()

    def set_interaction_enabled(self, enabled: bool) -> None:
        self._interaction_enabled = enabled
        self._apply_enabled_state()

    def set_opening(self, message: str | None) -> None:
        self._opening = message is not None
        if message is None:
            self.progress_label.clear()
            self.progress_container.hide()
        else:
            self.progress_label.setText(message)
            update_accessible_state(self.progress_label, message)
            self.progress_container.show()
        self._apply_enabled_state()

    def _apply_enabled_state(self) -> None:
        self.project_list.setEnabled(self._interaction_enabled and not self._opening)

    def _activate_project(self, item: QListWidgetItem) -> None:
        if not self.project_list.isEnabled() or not bool(item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        if bool(item.data(PROJECT_ACTIVE_ROLE)):
            self.return_to_current_requested.emit()
            return
        path = str(item.data(PROJECT_PATH_ROLE))
        name = str(item.data(PROJECT_NAME_ROLE))
        if self._project_dialog is not None:
            self._project_dialog.close()
        dialog = ProjectOpenChoiceDialog(name, path, self)
        dialog.current_window_requested.connect(self.open_current_requested)
        dialog.new_window_requested.connect(self.open_new_window_requested)
        dialog.destroyed.connect(lambda: self._clear_dialog(dialog))
        self._project_dialog = dialog
        dialog.open()

    def _clear_dialog(self, dialog: ProjectOpenChoiceDialog) -> None:
        if self._project_dialog is dialog:
            self._project_dialog = None


__all__ = ["PROJECT_ACTIVE_ROLE", "PROJECT_NAME_ROLE", "PROJECT_PATH_ROLE", "StartCenterProjectsPanel"]
