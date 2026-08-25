from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLayout, QMenu, QPushButton, QSizePolicy, QWidget

from transbridge.ui.foundation.tabler_icons import tabler_icon

from .theme_support import CHIP_STRUCTURE_STYLE, SmartAssistantTheme


class QuickActionsChips(QWidget):
    """Quick actions whose colour comes from the owning panel's snapshot."""

    action_clicked = pyqtSignal(str)
    skill_triggered = pyqtSignal(str)

    _ACTIONS = [
        ("翻译选中", "请翻译当前选中的词条", "language"),
        ("质量检查", "请检查当前集合的翻译质量", "shield-check"),
        ("术语查询", "请查询以下术语：", "book"),
        ("导出", "请导出当前集合为 JSON", "list-details"),
    ]

    def __init__(self, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._theme = theme or SmartAssistantTheme()
        self._buttons: list[QPushButton] = []
        self.setMaximumHeight(32)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("快捷操作")
        layout = QHBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for label, prompt, icon_name in self._ACTIONS:
            button = QPushButton(label)
            button.setProperty("tbIconName", icon_name)
            button.setAccessibleName(f"快捷操作：{label}")
            button.setToolTip(prompt)
            button.setStyleSheet(CHIP_STRUCTURE_STYLE)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked, value=prompt: self.action_clicked.emit(value))
            self._buttons.append(button)
            layout.addWidget(button)
        self._skill_btn = QPushButton("Skill")
        self._skill_btn.setProperty("tbIconName", "sparkles")
        self._skill_btn.setAccessibleName("选择 Skill")
        self._skill_btn.setToolTip("选择并执行自定义 Skill")
        self._skill_btn.setStyleSheet(CHIP_STRUCTURE_STYLE)
        self._skill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skill_btn.clicked.connect(self._show_skill_menu)
        self._buttons.append(self._skill_btn)
        layout.addWidget(self._skill_btn)
        layout.addStretch()
        self.apply_theme(self._theme)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = event.size().width()
        visible_count = 5 if width >= 390 else 4 if width >= 320 else 3 if width >= 240 else 2
        for index, button in enumerate(self._buttons):
            button.setVisible(index < visible_count)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        self._theme = theme
        for button in self._buttons:
            theme.apply_semantic(button, "muted", background=True)
            button.setIcon(tabler_icon(button, str(button.property("tbIconName")), 14))

    def _show_skill_menu(self) -> None:
        from transbridge.smart_assistant.skills import SkillRegistry

        skills = SkillRegistry.list_all()
        menu = QMenu(self)
        if not skills:
            menu.addAction("(无可用 Skill)").setEnabled(False)
        else:
            for spec in skills:
                action = menu.addAction(spec.display_name)
                action.setToolTip(spec.description)
                action.triggered.connect(lambda _checked, value=spec: self.skill_triggered.emit(value.name))
        menu.exec(self._skill_btn.mapToGlobal(self._skill_btn.rect().bottomLeft()))


__all__ = ["QuickActionsChips"]
