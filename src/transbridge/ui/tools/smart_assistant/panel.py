# TODO: i18n — 窗口标题"智能助手"为硬编码中文，待国际化改造
"""
SmartAssistantPanel 颜色面板:
  本文件无硬编码颜色值。所有视觉样式委托给 ChatWidget 和 MessageBubble。
  背景/边框由 QDockWidget 系统主题控制。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QSplitter
from PyQt6.QtCore import Qt, pyqtSignal

from .chat_widget import ChatWidget
from .session_list_widget import SessionListWidget
from .task_monitor import TaskMonitorWidget

logger = logging.getLogger(__name__)


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
        self.setMinimumWidth(900)
        self.setMinimumHeight(300)

        self._active_session_id: str | None = None
        self._init_skills()
        self._init_session_manager()
        self._init_ui(ctx)
        # 延迟到 ChatWidget UI 构建完成后再恢复会话（ChatWidget._init_ui 是 QTimer 延迟的）
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._restore_last_session)

    # ── 初始化 ──────────────────────────────────────────────────

    def _init_skills(self):
        from src.transbridge.config.paths import get_data_dir
        from src.transbridge.smart_assistant.skills import SkillRegistry
        SkillRegistry.reload(Path(get_data_dir()) / "skills")

    def _init_session_manager(self):
        from src.transbridge.config.paths import get_data_dir
        from src.transbridge.smart_assistant.session_manager import SessionManager
        self._session_mgr = SessionManager(get_data_dir())

    def _init_ui(self, ctx):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 水平分割：左侧会话列表 + 右侧（聊天区 + 任务监控）
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setHandleWidth(1)
        h_splitter.setStyleSheet("QSplitter::handle { background: #e0e0e0; }")

        self._session_list = SessionListWidget()
        self._session_list.create_session.connect(self._on_create_session)
        self._session_list.switch_session.connect(self._on_switch_session)
        self._session_list.delete_session.connect(self._on_delete_session)
        self._session_list.rename_session.connect(
            lambda sid, name: self._on_rename_session(sid, name))

        self._chat = ChatWidget(ctx)
        self._chat.set_session_manager(self._session_mgr)

        # 右侧垂直分割：聊天区 + 任务监控（7:3）
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(1)
        right_splitter.setStyleSheet("QSplitter::handle { background: #e0e0e0; }")
        right_splitter.addWidget(self._chat)
        self._task_monitor = TaskMonitorWidget()
        self._task_monitor.task_action.connect(self._on_task_action)
        right_splitter.addWidget(self._task_monitor)
        right_splitter.setStretchFactor(0, 7)
        right_splitter.setStretchFactor(1, 3)

        # 将 TaskMonitorWidget 引用传给 ChatWidget 用于刷新
        self._chat.set_task_monitor(self._task_monitor)

        h_splitter.addWidget(self._session_list)
        h_splitter.addWidget(right_splitter)
        h_splitter.setStretchFactor(0, 0)  # 会话列表不拉伸
        h_splitter.setStretchFactor(1, 1)  # 右侧自适应拉伸
        h_splitter.setSizes([220, 800])

        layout.addWidget(h_splitter, stretch=1)
        self.setWidget(container)
        # 设置面板默认宽度：侧边栏 220 + 聊天区 800 = 1020px
        container.setMinimumWidth(1020)
        self._refresh_session_list()

    def _restore_last_session(self):
        """启动时恢复上次活跃会话。若无会话则自动创建默认会话。"""
        if self._session_mgr.count() == 0:
            sid = self._session_mgr.create_session(project_name=self._get_project_name())
            self._active_session_id = sid
            self._refresh_session_list()
            return
        last_sid = self._session_mgr.get_last_active()
        if last_sid:
            self._active_session_id = last_sid
            self._refresh_session_list()
            self._session_list.set_active(last_sid)
            data = self._session_mgr.get_session(last_sid)
            if data and data.get("messages"):
                self._chat.load_history(data["messages"])

    # ── 会话操作 ──────────────────────────────────────────────

    def _on_create_session(self, name: str):
        sid = self._session_mgr.create_session(name=name, project_name=self._get_project_name())
        self._switch_to(sid)

    def _on_switch_session(self, session_id: str):
        self._switch_to(session_id)

    def _on_delete_session(self, session_id: str):
        self._session_mgr.delete_session(session_id)
        if self._active_session_id == session_id:
            self._active_session_id = None
            last = self._session_mgr.get_last_active()
            if last:
                self._on_switch_session(last)
            else:
                self._on_create_session("")
        self._refresh_session_list()

    def _on_rename_session(self, session_id: str, current_name: str):
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(
            self, "重命名会话", "新名称:",
            text=current_name,
        )
        if ok and new_name.strip():
            self._session_mgr.rename_session(session_id, new_name.strip())
            self._refresh_session_list()

    def _switch_to(self, session_id: str):
        if self._active_session_id == session_id:
            return
        # 保存当前会话
        if self._active_session_id:
            self._chat.save_current_session(self._active_session_id)
        # 加载目标会话
        self._active_session_id = session_id
        data = self._session_mgr.get_session(session_id)
        if data:
            self._chat.load_session(data)
        self._refresh_session_list()
        self._session_list.set_active(session_id)

    def _refresh_session_list(self):
        sessions = self._session_mgr.list_sessions()
        self._session_list.set_sessions(sessions)
        if self._active_session_id:
            self._session_list.set_active(self._active_session_id)

    def _get_project_name(self) -> str:
        try:
            ctx = self._chat._ctx
            if hasattr(ctx, 'active_project') and ctx.active_project:
                return getattr(ctx.active_project, 'name', '')
            if hasattr(ctx, 'esp_path') and ctx.esp_path:
                return Path(ctx.esp_path).stem
        except Exception:
            pass
        return ""

    # ── 任务监控操作 ──────────────────────────────────────────

    def _on_task_action(self, task_id: str, action: str):
        """处理 TaskMonitorWidget 发出的操作信号。"""
        from src.transbridge.smart_assistant.tools.task_manager import TaskManager
        tm = TaskManager()
        if action == "cleanup_completed":
            for tid in tm.list_all():
                status = tm.get_status(tid).get("status", "")
                if status in ("completed", "failed", "cancelled"):
                    tm.cleanup(tid)
        elif action == "cleanup" and task_id != "__all__":
            tm.cleanup(task_id)
        elif action == "cancel":
            tm.cancel(task_id)
        elif action == "pause":
            tm.pause(task_id)
        elif action == "resume":
            tm.resume(task_id)
        # 操作后立即刷新
        self._chat._refresh_task_monitor()

    # ── 事件 ──────────────────────────────────────────────────

    def showEvent(self, event):
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def closeEvent(self, event):
        if self._active_session_id:
            try:
                self._chat.save_current_session(self._active_session_id)
            except Exception:
                logger.debug("关闭时保存会话失败", exc_info=True)
        self._chat.shutdown()
        super().closeEvent(event)

    # ── 公共访问 ──────────────────────────────────────────────

    @property
    def chat(self) -> ChatWidget:
        return self._chat
