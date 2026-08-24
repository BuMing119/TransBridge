"""A compact menu bar that reveals the existing application menus in place."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCursor, QEnterEvent, QFocusEvent, QMouseEvent
from PyQt6.QtWidgets import QMenuBar, QWidget


class ProgressiveMenuBar(QMenuBar):
    """Keep one authoritative menu tree while switching its top-level presentation."""

    expanded_changed = pyqtSignal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        collapse_delay_ms: int = 350,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tbProgressiveMenuBar")
        self.setAccessibleName("TransBridge 主菜单")
        self.setAccessibleDescription("鼠标移入或键盘聚焦后显示完整应用菜单")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._menu_actions: tuple[QAction, ...] = ()
        self._expanded = False
        self._keyboard_focus_active = False
        self._compact_action = QAction("☰", self)
        self._compact_action.setToolTip("展开主菜单")
        self._compact_action.setStatusTip("显示文件、项目、翻译、设置和帮助等完整菜单")
        self._compact_action.triggered.connect(self.expand)
        self.addAction(self._compact_action)

        self._collapse_timer = QTimer(self)
        self._collapse_timer.setSingleShot(True)
        self._collapse_timer.setInterval(max(0, collapse_delay_ms))
        self._collapse_timer.timeout.connect(self._collapse_if_idle)

    @property
    def is_expanded(self) -> bool:
        return self._expanded

    @property
    def compact_action(self) -> QAction:
        return self._compact_action

    @property
    def menu_actions(self) -> tuple[QAction, ...]:
        return self._menu_actions

    @property
    def collapse_timer(self) -> QTimer:
        return self._collapse_timer

    def bind_existing_menus(self) -> None:
        """Capture top-level menus already created by ``MenuBuilder``."""

        actions = tuple(action for action in self.actions() if action is not self._compact_action)
        if actions == self._menu_actions:
            self.collapse()
            return
        self._menu_actions = actions
        for action in actions:
            menu = action.menu()
            if menu is not None:
                menu.aboutToShow.connect(self._keep_expanded)
                menu.aboutToHide.connect(self._menu_hidden)
        self.collapse()

    def expand(self) -> None:
        self._collapse_timer.stop()
        if self._expanded:
            return
        self._expanded = True
        self._compact_action.setVisible(False)
        for action in self._menu_actions:
            action.setVisible(True)
        self.setProperty("tbMenuExpanded", True)
        self.updateGeometry()
        self.update()
        self.expanded_changed.emit(True)

    def collapse(self) -> None:
        self._collapse_timer.stop()
        if self._has_open_menu():
            return
        if (
            not self._expanded
            and self._compact_action.isVisible()
            and not any(action.isVisible() for action in self._menu_actions)
        ):
            return
        self._expanded = False
        for action in self._menu_actions:
            action.setVisible(False)
        self._compact_action.setVisible(True)
        self.setProperty("tbMenuExpanded", False)
        self.updateGeometry()
        self.update()
        self.expanded_changed.emit(False)

    def schedule_collapse(self) -> None:
        if self._expanded and not self._has_open_menu():
            self._collapse_timer.start()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt API
        self._collapse_timer.stop()
        if not self._expanded and self.actionAt(event.position().toPoint()) is self._compact_action:
            self.expand()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        self.schedule_collapse()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if not self._expanded and self.actionAt(event.position().toPoint()) is self._compact_action:
            self.expand()
        else:
            self._collapse_timer.stop()
        super().mouseMoveEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt API
        self._keyboard_focus_active = event.reason() in {
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
            Qt.FocusReason.MenuBarFocusReason,
        }
        if self._keyboard_focus_active:
            self.expand()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt API
        self._keyboard_focus_active = False
        self.schedule_collapse()
        super().focusOutEvent(event)

    def _keep_expanded(self) -> None:
        self._collapse_timer.stop()
        self.expand()

    def _menu_hidden(self) -> None:
        QTimer.singleShot(0, self._schedule_if_pointer_left)

    def _schedule_if_pointer_left(self) -> None:
        local_pointer = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(local_pointer) and not self._keyboard_focus_active:
            self.schedule_collapse()

    def _collapse_if_idle(self) -> None:
        local_pointer = self.mapFromGlobal(QCursor.pos())
        if self._has_open_menu() or self.rect().contains(local_pointer) or self._keyboard_focus_active:
            return
        self.collapse()

    def _has_open_menu(self) -> bool:
        for action in self._menu_actions:
            menu = action.menu()
            if menu is not None and menu.isVisible():
                return True
        return False


__all__ = ["ProgressiveMenuBar"]
