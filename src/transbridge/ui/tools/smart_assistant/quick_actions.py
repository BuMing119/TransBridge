from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QMenu
from PyQt6.QtCore import Qt, pyqtSignal


class QuickActionsChips(QWidget):
    """快捷指令 chips 标签行，嵌入 ChatWidget 输入区上方。"""

    action_clicked = pyqtSignal(str)
    skill_triggered = pyqtSignal(str)

    _ACTIONS = [
        ("翻译选中", "请翻译当前选中的词条"),
        ("质量检查", "请检查当前集合的翻译质量"),
        ("查询术语", "请查询以下术语："),
        ("导出JSON", "请导出当前集合为 JSON"),
    ]

    _CHIP_STYLE = (
        "QPushButton {"
        "  background-color: #f0f0f0; border: 1px solid #e0e0e0;"
        "  border-radius: 12px; padding: 3px 10px;"
        "  font-size: 11px; color: #555;"
        "}"
        "QPushButton:hover { background-color: #e0e0e0; color: #333; }"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(32)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        for label, prompt in self._ACTIONS:
            btn = QPushButton(label)
            btn.setToolTip(prompt)
            btn.setStyleSheet(self._CHIP_STYLE)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, p=prompt: self.action_clicked.emit(p))
            layout.addWidget(btn)

        # Skill 下拉 chip
        self._skill_btn = QPushButton("Skill")
        self._skill_btn.setToolTip("选择并执行自定义 Skill")
        self._skill_btn.setStyleSheet(self._CHIP_STYLE)
        self._skill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._skill_btn.clicked.connect(self._show_skill_menu)
        layout.addWidget(self._skill_btn)

        layout.addStretch()

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
                action.triggered.connect(
                    lambda checked, s=spec: self.skill_triggered.emit(s.name)
                )
        menu.exec(
            self._skill_btn.mapToGlobal(self._skill_btn.rect().bottomLeft())
        )
