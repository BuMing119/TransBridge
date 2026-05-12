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
        self.setMinimumHeight(200)

        # 加载 Skills
        from src.transbridge.config.paths import get_data_dir
        from pathlib import Path
        from src.transbridge.smart_assistant.skills import SkillRegistry
        SkillRegistry.reload(Path(get_data_dir()) / "skills")

        # ── 主容器：ChatWidget 独立，chips 在其内部 ──
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
        """M13: 关闭面板时清理运行中的 worker/engine 和 memory_store。"""
        if self._chat._worker and self._chat._worker.isRunning():
            self._chat._worker.cancel()
            self._chat._worker.wait(3000)
        if self._chat._engine:
            self._chat._engine.cancel()
        if self._chat._memory_store:
            self._chat._memory_store.close()
        super().closeEvent(event)

    # ── 公共访问 ──────────────────────────────────────────────

    @property
    def chat(self) -> ChatWidget:
        return self._chat
