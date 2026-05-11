from PyQt6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QFrame
from PyQt6.QtCore import Qt, pyqtSignal

from .quick_actions import QuickActionsPanel
from .chat_widget import ChatWidget


class SmartAssistantPanel(QDockWidget):
    """智能助手底部面板，停靠在 MainWindow 底部（类似 IDE 终端）。"""

    visibility_changed = pyqtSignal(bool)

    def __init__(self, ctx, parent=None):
        super().__init__("智能助手", parent)
        self.setObjectName("SmartAssistantPanel")

        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea |
            Qt.DockWidgetArea.RightDockWidgetArea |
            Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable |
            QDockWidget.DockWidgetFeature.DockWidgetMovable |
            QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.setMinimumHeight(200)

        # ── 主容器 ──
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._quick_actions = QuickActionsPanel()
        layout.addWidget(self._quick_actions)

        # 加载 Skills
        from src.transbridge.config.paths import get_data_dir
        from pathlib import Path
        from src.transbridge.smart_assistant.skills import SkillRegistry, SkillLoader
        SkillRegistry.reload(Path(get_data_dir()) / "skills")

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        self._chat = ChatWidget(ctx)
        layout.addWidget(self._chat, stretch=1)

        # 快捷指令 → 填入输入框
        self._quick_actions.action_clicked.connect(self._chat.set_input)
        self._quick_actions.skill_triggered.connect(self._chat._on_skill)

        self.setWidget(container)

    # ── 事件 ──────────────────────────────────────────────────

    def showEvent(self, event):
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    # ── 公共访问 ──────────────────────────────────────────────

    @property
    def chat(self) -> ChatWidget:
        return self._chat
