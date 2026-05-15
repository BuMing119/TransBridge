# TODO: i18n — 窗口标题"智能助手"为硬编码中文，待国际化改造
"""
SmartAssistantPanel 颜色面板:
  本文件无硬编码颜色值。所有视觉样式委托给 ChatWidget 和 MessageBubble。
  背景/边框由 QDockWidget 系统主题控制。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QFrame
from PyQt6.QtCore import Qt, pyqtSignal

from .chat_widget import ChatWidget


class SmartAssistantPanel(QDockWidget):
    """智能助手面板，停靠在 MainWindow 底部/侧边（类似 IDE 终端）。"""

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
        self.setMinimumWidth(400)
        self.setMinimumHeight(300)

        self._init_skills()
        self._init_ui(ctx)

    # ── 初始化 ──────────────────────────────────────────────────

    def _init_skills(self):
        """加载 Skills 注册表。"""
        from src.transbridge.config.paths import get_data_dir
        from pathlib import Path
        from src.transbridge.smart_assistant.skills import SkillRegistry
        SkillRegistry.reload(Path(get_data_dir()) / "skills")

    def _init_ui(self, ctx):
        """初始化主 UI 容器和 ChatWidget。"""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._chat = ChatWidget(ctx)
        layout.addWidget(self._chat, stretch=1)

        self.setWidget(container)

    # ── 事件 ──────────────────────────────────────────────────

    def showEvent(self, event):
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def closeEvent(self, event):
        """关闭面板时清理所有资源（B8: 委托给 ChatWidget.shutdown）。"""
        self._chat.shutdown()
        super().closeEvent(event)

    # ── 公共访问 ──────────────────────────────────────────────

    @property
    def chat(self) -> ChatWidget:
        return self._chat
