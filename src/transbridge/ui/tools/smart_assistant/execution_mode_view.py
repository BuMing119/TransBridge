"""An always-visible execution mode with the existing confirmation policy."""

from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QPushButton

from .theme_support import QUIET_BUTTON_STYLE


class ExecutionModeButton(QPushButton):
    def __init__(self, action: QAction, parent=None):
        super().__init__(parent)
        self._action = action
        self.setStyleSheet(
            QUIET_BUTTON_STYLE + "QPushButton { padding-right: 16px; }"
            "QPushButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; "
            "right: 4px; width: 8px; height: 8px; }"
        )
        self.setFixedHeight(32)
        self.setAccessibleName("执行模式")
        self.menu = QMenu(self)
        group = QActionGroup(self.menu)
        self.confirm_action = self.menu.addAction("执行前确认")
        self.automatic_action = self.menu.addAction("自动执行常规操作")
        for choice in (self.confirm_action, self.automatic_action):
            choice.setCheckable(True)
            group.addAction(choice)
        self.confirm_action.triggered.connect(lambda: action.setChecked(False))
        self.automatic_action.triggered.connect(lambda: action.setChecked(True))
        self.setMenu(self.menu)
        action.toggled.connect(self.refresh)
        self.refresh()

    def refresh(self, automatic=None):
        automatic = self._action.isChecked() if automatic is None else automatic
        label = "自动执行常规操作" if automatic else "执行前确认"
        self.setText("自动执行" if automatic else "先确认")
        self.confirm_action.setChecked(not automatic)
        self.automatic_action.setChecked(automatic)
        explanation = (
            "常规单步操作可直接执行；执行计划、高权限和标记需确认的操作仍需确认。"
            if automatic
            else "执行工具操作前先展示确认；停止当前任务等控制操作会直接生效。"
        )
        self.setToolTip(f"{label}：{explanation}")
        self.setAccessibleDescription(f"当前模式：{label}。{explanation}")
