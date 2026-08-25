# TODO: i18n — 窗口标题"智能助手"为硬编码中文，待国际化改造
"""
SmartAssistantPanel 颜色面板:
  本文件无硬编码颜色值。所有颜色来自 Qt palette 与 SmartAssistantTheme。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDockWidget, QFrame, QSizePolicy, QSplitter, QVBoxLayout

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windows_taskbar import clear_window_app_user_model_id, set_window_app_user_model_id

from .chat_widget import ChatWidget
from .panel_header import AssistantPanelHeader
from .session_list_widget import SessionListWidget
from .task_monitor import TaskMonitorWidget
from .theme_support import BODY_STRUCTURE_STYLE, PANEL_STRUCTURE_STYLE, SmartAssistantTheme

logger = logging.getLogger(__name__)


class SmartAssistantPanel(QDockWidget):
    """智能助手浮动覆盖面板；不允许重新停靠并压缩主工作台。"""

    MINIMUM_DOCK_WIDTH = 400
    MINIMUM_DOCK_HEIGHT = 220
    TASKBAR_APP_USER_MODEL_ID = "TransBridge.SmartAssistant"

    visibility_changed = pyqtSignal(bool)

    def __init__(
        self,
        ctx,
        parent=None,
        *,
        session_commands=None,
        session_projection=None,
        runtime_context=None,
        theme_view: ThemeView | None = None,
    ):
        super().__init__("智能助手", parent)
        flags = self.windowFlags()
        flags &= ~Qt.WindowType.WindowType_Mask
        self.setWindowFlags(flags | Qt.WindowType.Window)
        self.setObjectName("SmartAssistantPanel")
        self.setStyleSheet(PANEL_STRUCTURE_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("智能助手")

        self.setAllowedAreas(Qt.DockWidgetArea.NoDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setMinimumWidth(self.MINIMUM_DOCK_WIDTH)
        self.setMinimumHeight(self.MINIMUM_DOCK_HEIGHT)

        self._active_session_id: str | None = None
        self._session_commands = session_commands
        self._session_projection = session_projection
        self._runtime_context = runtime_context
        self._session_subscription = None
        self._theme_view = theme_view
        self._theme_subscription = None
        self._theme = SmartAssistantTheme(None if theme_view is None else theme_view.snapshot())
        self._theme_revision = self._theme.revision
        self._disposed = False
        self._taskbar_identity_applied = False
        self._header = AssistantPanelHeader(theme=self._theme)
        self._header.close_requested.connect(self.close)
        self._header.minimize_requested.connect(self._minimize_panel)
        self._header.set_model_name(self._configured_model_name())
        self.setTitleBarWidget(self._header)
        self._init_skills()
        self._init_session_manager()
        self._init_ui(ctx)
        if self._theme_view is not None:
            self._theme_subscription = self._theme_view.subscribe(self, self._on_theme_changed)
        if self._session_projection is not None:
            self._session_subscription = self._session_projection.subscribe(self._on_session_projection)
        # Prepare the native HWND before its first ShowWindow call. Windows
        # decides taskbar eligibility when the top-level window first appears.
        self._prepare_taskbar_identity()
        # 延迟到 ChatWidget UI 构建完成后再恢复会话（ChatWidget._init_ui 是 QTimer 延迟的）
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(0, self._restore_last_session)

    # ── 初始化 ──────────────────────────────────────────────────

    def _init_skills(self):
        from transbridge.config.paths import get_data_dir
        from transbridge.smart_assistant.skills import SkillRegistry

        SkillRegistry.reload(Path(get_data_dir()) / "skills")

    def _init_session_manager(self):
        if self._session_commands is not None:
            self._session_mgr = None
            return
        from transbridge.config.paths import get_data_dir
        from transbridge.smart_assistant.session_manager import SessionManager

        self._session_mgr = SessionManager(get_data_dir())

    def _init_ui(self, ctx):
        container = QFrame()
        container.setObjectName("smartAssistantBody")
        container.setStyleSheet(BODY_STRUCTURE_STYLE)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 水平分割：左侧会话列表 + 右侧（聊天区 + 任务监控）
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.setHandleWidth(1)

        self._session_list = SessionListWidget(theme=self._theme)
        self._session_list.create_session.connect(self._on_create_session)
        self._session_list.switch_session.connect(self._on_switch_session)
        self._session_list.delete_session.connect(self._on_delete_session)
        self._session_list.rename_session.connect(lambda sid, name: self._on_rename_session(sid, name))

        self._chat = ChatWidget(ctx, theme=self._theme)
        self._chat.configure_session_port(
            active_session_id=self._current_session_id,
            refresh_sessions=self._refresh_session_list,
        )
        if self._session_mgr is not None:
            self._chat.set_session_manager(self._session_mgr)

        # 右侧垂直分割：聊天区 + 任务监控（7:3）
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(1)
        right_splitter.addWidget(self._chat)
        self._task_monitor = TaskMonitorWidget(theme=self._theme)
        self._task_monitor.task_action.connect(self._on_task_action)
        right_splitter.addWidget(self._task_monitor)
        right_splitter.setStretchFactor(0, 7)
        right_splitter.setStretchFactor(1, 3)
        right_splitter.setSizes([760, 36])

        # 将 TaskMonitorWidget 引用传给 ChatWidget 用于刷新
        self._chat.set_task_monitor(self._task_monitor)

        h_splitter.addWidget(self._session_list)
        h_splitter.addWidget(right_splitter)
        h_splitter.setStretchFactor(0, 0)  # 会话列表不拉伸
        h_splitter.setStretchFactor(1, 1)  # 右侧自适应拉伸
        h_splitter.setSizes([240, 960])

        layout.addWidget(h_splitter, stretch=1)
        self._body = container
        self._theme.apply_surface(container)
        self.setWidget(container)
        # Dock 在已显示的主窗口中动态加入。忽略内容的纵向 sizeHint，避免
        # QMainWindow 为保留工作台高度而把新增 Dock 撑到屏幕之外；明确的
        # Dock 最小高度仍保证输入区和任务标题可达。
        container_policy = container.sizePolicy()
        container_policy.setVerticalPolicy(QSizePolicy.Policy.Ignored)
        container.setSizePolicy(container_policy)
        self._refresh_session_list()

    @staticmethod
    def _configured_model_name() -> str:
        """Read only the public model identifier; credentials never enter the title bar."""
        try:
            from transbridge.config.repository import default_config_repository

            model = default_config_repository().load().value("llm", "model", "")
        except Exception:
            logger.debug("读取智能助手模型名称失败", exc_info=True)
            return "模型未配置"
        return str(model).strip() or "模型未配置"

    def _minimize_panel(self) -> None:
        if not self.isFloating():
            self.setFloating(True)
        self.showMinimized()

    def _prepare_taskbar_identity(self) -> None:
        if not getattr(self, "_taskbar_identity_applied", False):
            self._taskbar_identity_applied = set_window_app_user_model_id(self, self.TASKBAR_APP_USER_MODEL_ID)

    def _restore_last_session(self):
        """启动时恢复上次活跃会话。若无会话则自动创建默认会话。"""
        if self._session_commands is not None:
            if self._current_session_id() is None:
                sessions = self._session_commands.list_sessions()
                if sessions:
                    self._switch_to(sessions[0]["session_id"])
                else:
                    self._create_authoritative_session("")
            return
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
        if self._session_commands is not None:
            self._persist_authoritative_chat()
            self._create_authoritative_session(name)
            return
        sid = self._session_mgr.create_session(name=name, project_name=self._get_project_name())
        self._switch_to(sid)

    def _on_switch_session(self, session_id: str):
        self._switch_to(session_id)

    def _on_delete_session(self, session_id: str):
        if self._session_commands is not None:
            logger.warning("V2 Session deletion requires an explicit repository command")
            return
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
        if self._session_commands is not None:
            logger.warning("V2 Session rename requires an explicit aggregate command")
            return
        from PyQt6.QtWidgets import QInputDialog

        new_name, ok = QInputDialog.getText(
            self,
            "重命名会话",
            "新名称:",
            text=current_name,
        )
        if ok and new_name.strip():
            self._session_mgr.rename_session(session_id, new_name.strip())
            self._refresh_session_list()

    def _switch_to(self, session_id: str):
        if self._session_commands is not None:
            if self._current_session_id() == session_id:
                return
            self._persist_authoritative_chat()
            from transbridge.persistence.v2 import SessionId, SessionRef

            result = self._session_commands.switch(
                SessionRef(SessionId(session_id)),
                self._runtime_context,
            )
            if not result.is_success:
                logger.warning("V2 Session switch refused: %s", result.diagnostics[0].code)
            return
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
        if self._session_commands is not None:
            sessions = self._session_commands.list_sessions()
            self._session_list.set_sessions(sessions)
            current = self._current_session_id()
            if current is not None:
                self._session_list.set_active(current)
            return
        sessions = self._session_mgr.list_sessions()
        self._session_list.set_sessions(sessions)
        if self._active_session_id:
            self._session_list.set_active(self._active_session_id)

    def _get_project_name(self) -> str:
        try:
            ctx = self._chat.context
            if hasattr(ctx, "active_project") and ctx.active_project:
                return getattr(ctx.active_project, "name", "")
            if hasattr(ctx, "esp_path") and ctx.esp_path:
                return Path(ctx.esp_path).stem
        except Exception:
            pass
        return ""

    # ── 任务监控操作 ──────────────────────────────────────────

    def _on_task_action(self, task_id: str, action: str):
        """处理 TaskMonitorWidget 发出的操作信号。"""
        from transbridge.smart_assistant.tools.task_manager import TaskManager

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
        self._chat.refresh_task_monitor()

    # ── 事件 ──────────────────────────────────────────────────

    def showEvent(self, event):
        # A distinct window-level AppUserModelID prevents Windows from grouping
        # this top-level window under the main TransBridge taskbar button.
        if not getattr(self, "_taskbar_identity_applied", False):
            self._prepare_taskbar_identity()
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def closeEvent(self, event):
        """The window close button hides this reusable panel."""
        if self._session_commands is not None:
            self._persist_authoritative_chat()
        elif self._active_session_id:
            try:
                self._chat.save_current_session(self._active_session_id)
            except Exception:
                logger.debug("关闭时保存会话失败", exc_info=True)
        super().closeEvent(event)

    @property
    def is_disposed(self) -> bool:
        return self._disposed

    def dispose(self, *, wait_for_worker: bool = True) -> None:
        if self._disposed:
            return
        self._disposed = True
        if getattr(self, "_taskbar_identity_applied", False):
            clear_window_app_user_model_id(self)
            self._taskbar_identity_applied = False
        self._chat.shutdown(wait_for_worker=wait_for_worker)
        if self._session_subscription is not None:
            self._session_subscription.close()
            self._session_subscription = None
        theme_subscription = getattr(self, "_theme_subscription", None)
        if theme_subscription is not None:
            theme_subscription.close()
            self._theme_subscription = None

    @property
    def theme_revision(self) -> int:
        return self._theme_revision

    def _on_theme_changed(self, snapshot) -> None:
        if self._disposed or snapshot.revision == self._theme_revision:
            return
        self._theme.update(snapshot)
        self._theme_revision = snapshot.revision
        self._header.apply_theme(self._theme)
        body = getattr(self, "_body", None)
        if body is not None:
            self._theme.apply_surface(body)
        self._session_list.apply_theme(self._theme)
        self._chat.apply_theme(self._theme)
        self._task_monitor.apply_theme(self._theme)

    def _current_session_id(self) -> str | None:
        if self._session_projection is None:
            return self._active_session_id
        snapshot = self._session_projection.snapshot()
        if snapshot is None:
            return None
        value = snapshot.to_dict()["values"].get("session_id")
        return None if value is None else str(value)

    def _create_authoritative_session(self, name: str) -> None:
        if self._runtime_context is None:
            logger.error("V2 Session creation requires RuntimeContext")
            return
        result = self._session_commands.create_and_activate(name, self._runtime_context)
        if not result.is_success:
            logger.warning("V2 Session creation failed: %s", result.diagnostics[0].code)

    def _persist_authoritative_chat(self) -> None:
        session_id = self._current_session_id()
        if session_id is None or self._runtime_context is None:
            return
        from transbridge.persistence.v2 import SessionId, SessionRef

        backend, controller = self._chat.recovery_snapshot()
        result = self._session_commands.save_conversation(
            SessionRef(SessionId(session_id)),
            list(backend),
            list(backend),
            self._runtime_context,
            controller=controller,
        )
        if not result.is_success:
            logger.warning("V2 Session save failed: %s", result.diagnostics[0].code)

    def _on_session_projection(self, snapshot) -> None:
        if not hasattr(self, "_session_list"):
            return
        self._refresh_session_list()
        if snapshot is None:
            return
        values = snapshot.to_dict()["values"]
        self._chat.load_session(values)

    # ── 公共访问 ──────────────────────────────────────────────

    @property
    def chat(self) -> ChatWidget:
        return self._chat
