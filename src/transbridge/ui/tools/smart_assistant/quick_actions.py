from PyQt6.QtWidgets import QWidget, QHBoxLayout, QPushButton, QMenu
from PyQt6.QtCore import pyqtSignal


class QuickActionsPanel(QWidget):
    """顶部快捷指令面板，提供常用操作和 Skill 的快捷入口。"""

    action_clicked = pyqtSignal(str)
    skill_triggered = pyqtSignal(str)  # skill name

    _ACTIONS = [
        ("翻译选中", "请翻译当前选中的词条"),
        ("质量检查", "请检查当前集合的翻译质量"),
        ("查询术语", "请查询以下术语："),
        ("导出JSON", "请导出当前集合为 JSON"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaximumHeight(48)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        for label, prompt in self._ACTIONS:
            btn = QPushButton(label)
            btn.setToolTip(prompt)
            btn.setMinimumHeight(36)
            btn.clicked.connect(lambda checked, p=prompt: self.action_clicked.emit(p))
            layout.addWidget(btn)

        # Skill 下拉按钮
        self._skill_btn = QPushButton("Skill")
        self._skill_btn.setToolTip("选择并执行自定义 Skill")
        self._skill_btn.setMinimumHeight(36)
        self._skill_btn.clicked.connect(self._show_skill_menu)
        layout.addWidget(self._skill_btn)

        layout.addStretch()

    def _show_skill_menu(self) -> None:
        from src.transbridge.smart_assistant.skills import SkillRegistry
        skills = SkillRegistry.list_all()
        menu = QMenu(self)
        if not skills:
            menu.addAction("(无可用 Skill)").setEnabled(False)
        else:
            for spec in skills:
                action = menu.addAction(f"{spec.display_name}")
                action.setToolTip(spec.description)
                action.triggered.connect(
                    lambda checked, s=spec: self.skill_triggered.emit(s.name)
                )
        menu.exec(self._skill_btn.mapToGlobal(self._skill_btn.rect().bottomLeft()))
