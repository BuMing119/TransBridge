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
        """M13+M4+m3: 关闭面板时清理 worker/engine/memory_store/TaskManager。"""
        # m3: 确保活跃观测追踪被正确关闭（防止 trace 数据丢失）
        if hasattr(self._chat, '_obs_collector') and self._chat._obs_collector:
            try:
                self._chat._obs_collector.end_conversation()
            except Exception:
                pass

        # CR9: 清理 ObservabilityCollector 回调，解除引用
        try:
            if hasattr(self._chat, '_obs_collector') and self._chat._obs_collector:
                self._chat._obs_collector._on_token_stats_updated = None
        except Exception:
            pass
        try:
            from src.transbridge.smart_assistant.tools.task_manager import TaskManager
            tm = TaskManager()
            tm.remove_listener(self._chat._on_task_completed)
            tm.remove_listener(self._chat._on_task_failed)
        except Exception:
            pass

        if self._chat._worker and self._chat._worker.is_alive():
            self._chat._worker.cancel()
            self._chat._worker.join(timeout=3)
        if self._chat._engine:
            self._chat._engine.cancel()
        if self._chat._memory_store:
            self._chat._memory_store.close()
        # MA4: 重置 TaskManager 单例，防止会话间泄漏
        try:
            from src.transbridge.smart_assistant.tools.task_manager import TaskManager
            TaskManager.reset()
        except Exception:
            pass
        super().closeEvent(event)

    # ── 公共访问 ──────────────────────────────────────────────

    @property
    def chat(self) -> ChatWidget:
        return self._chat
