# TODO: i18n — 窗口标题"TransBridge AI 助手"为硬编码中文，待国际化改造
"""
SmartAssistantPanel 颜色面板:
  本文件无硬编码颜色值。所有颜色来自 Qt palette 与 SmartAssistantTheme。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDockWidget, QFrame, QMessageBox, QSplitter, QVBoxLayout

from transbridge.smart_assistant.service_readiness import read_service_availability
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.windows_taskbar import clear_window_app_user_model_id, set_window_app_user_model_id

from .chat_widget import ChatWidget
from .panel_header import AssistantPanelHeader
from .panel_layout import AssistantColumns
from .panel_sessions import PanelSessions
from .session_list_widget import SessionListWidget
from .session_workspace import SessionWorkspace
from .task_monitor import TaskMonitorWidget
from .theme_support import BODY_STRUCTURE_STYLE, PANEL_STRUCTURE_STYLE, SmartAssistantTheme
from .window_presentation import AssistantWindowPresentation, configured_model_name

logger = logging.getLogger(__name__)


class SmartAssistantPanel(QDockWidget):
    """智能助手浮动覆盖面板；不允许重新停靠并压缩主工作台。"""

    MINIMUM_DOCK_WIDTH = 480
    MINIMUM_DOCK_HEIGHT = 400
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
        navigation=None,
    ):
        super().__init__("TransBridge AI 助手", parent)
        self.setObjectName("SmartAssistantPanel")
        self.setProperty("tbTaskDialog", True)
        self.setStyleSheet(PANEL_STRUCTURE_STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("智能助手")

        self.setAllowedAreas(Qt.DockWidgetArea.NoDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setMinimumWidth(self.MINIMUM_DOCK_WIDTH)
        self.setMinimumHeight(self.MINIMUM_DOCK_HEIGHT)

        self._active_session_id: str | None = None
        self._saving_session_id: str | None = None
        self._session_commands = session_commands
        self._session_projection = session_projection
        self._runtime_context = runtime_context
        self._navigation = dict(navigation or {})
        self._session_subscription = None
        self._theme_view = theme_view
        self._theme_subscription = None
        self._theme = SmartAssistantTheme(None if theme_view is None else theme_view.snapshot())
        self._theme_revision = self._theme.revision
        self._disposed = False
        self._taskbar_identity_applied = False
        self._header = AssistantPanelHeader(theme=self._theme)
        self._window_presentation = AssistantWindowPresentation(
            self, self._header, self._configured_service_availability
        )
        self._init_skills()
        self._init_session_manager()
        self._sessions = PanelSessions(self)
        self._workspace = SessionWorkspace(self, ctx)
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
        from transbridge.config.paths import get_data_resource_dir
        from transbridge.smart_assistant.skills import SkillRegistry

        SkillRegistry.reload(Path(get_data_resource_dir("skills")))

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
        # Keep session/model information in the client area. A custom dock title
        # bar would suppress the platform's native frame and window controls.
        layout.addWidget(self._header)

        # 水平分割：左侧会话列表 + 右侧（聊天区 + 任务监控）
        h_splitter = AssistantColumns()

        self._session_list = SessionListWidget(theme=self._theme)
        self._session_list.create_session.connect(self._on_create_session)
        self._session_list.switch_session.connect(self._on_switch_session)
        self._session_list.delete_session.connect(self._on_delete_session)
        self._session_list.rename_session.connect(lambda sid, name: self._on_rename_session(sid, name))

        self._chat = ChatWidget(
            ctx,
            theme=self._theme,
            navigation={
                **self._navigation,
                **(
                    {
                        "session_scope_ready": self._workspace.ready,
                        "ensure_session_scope": self._workspace.ensure,
                    }
                    if self._session_commands is not None
                    else {}
                ),
                "model_configured": lambda: self._header.model_configured,
                "service_availability": lambda: self._header.service_availability,
                "refresh_service_availability": self._window_presentation.refresh,
            },
        )
        self._chat.configure_session_port(
            active_session_id=self._current_session_id,
            refresh_sessions=self._refresh_session_list,
            save_session=self._persist_authoritative_chat if self._session_commands is not None else None,
            request_context=self._workspace.bound_context,
            assistant_requests=getattr(self._session_commands, "assistant_requests", None),
        )
        if self._session_mgr is not None:
            self._chat.set_session_manager(self._session_mgr)

        # 任务明细默认收起；没有任务时完全隐藏。
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setHandleWidth(1)
        right_splitter.setChildrenCollapsible(False)
        chat_container = QFrame()
        chat_layout = QVBoxLayout(chat_container)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        chat_layout.addWidget(self._workspace.banner)
        chat_layout.addWidget(self._chat)
        right_splitter.addWidget(chat_container)
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
        h_splitter.setSizes([200, 960])

        layout.addWidget(h_splitter, stretch=1)
        self._body = container
        self._theme.apply_surface(container)
        self.setWidget(container)
        self._refresh_session_list()

    @staticmethod
    def _configured_model_name() -> str:
        return configured_model_name()

    @staticmethod
    def _configured_service_availability():
        return read_service_availability()

    def _prepare_taskbar_identity(self) -> None:
        if not getattr(self, "_taskbar_identity_applied", False):
            self._taskbar_identity_applied = set_window_app_user_model_id(self, self.TASKBAR_APP_USER_MODEL_ID)

    def _restore_last_session(self):
        return self._sessions.restore()

    # ── 会话操作 ──────────────────────────────────────────────

    def _on_create_session(self, name: str):
        return self._sessions.create(name)

    def _on_switch_session(self, session_id: str):
        self._switch_to(session_id)

    def _on_delete_session(self, session_id: str):
        return self._sessions.delete(session_id)

    def _on_rename_session(self, session_id: str, current_name: str):
        return self._sessions.rename(session_id, current_name)

    def _switch_to(self, session_id: str):
        return self._sessions.switch(session_id)

    def _refresh_session_list(self):
        return self._sessions.refresh()

    def _get_project_name(self) -> str:
        try:
            ctx = self._chat.context
            if ctx.project_name:
                return ctx.project_name
            if hasattr(ctx, "esp_path") and ctx.esp_path:
                return Path(ctx.esp_path).stem
        except Exception:
            pass
        return ""

    # ── 任务监控操作 ──────────────────────────────────────────

    def _on_task_action(self, task_id: str, action: str):
        """处理 TaskMonitorWidget 发出的操作信号。"""
        from transbridge.smart_assistant.tools.task_manager import TaskManager

        from .task_monitor_actions import apply_monitor_action

        if (
            self._session_commands is not None
            and action == "resume"
            and not self._workspace.ensure("继续任务", lambda: self._on_task_action(task_id, action))
        ):
            return
        apply_monitor_action(TaskManager(), self._task_monitor, self._chat._session_runtime.scope(), task_id, action)
        # 操作后立即刷新
        self._chat.refresh_task_monitor()

    # ── 事件 ──────────────────────────────────────────────────

    def showEvent(self, event):
        # A distinct window-level AppUserModelID prevents Windows from grouping
        # this top-level window under the main TransBridge taskbar button.
        if not getattr(self, "_taskbar_identity_applied", False):
            self._prepare_taskbar_identity()
        self.visibility_changed.emit(True)
        binding = getattr(getattr(self, "_chat", None), "_request_binding", None)
        if binding is not None:
            binding.set_active(True)
        super().showEvent(event)

    def hideEvent(self, event):
        self.visibility_changed.emit(False)
        binding = getattr(getattr(self, "_chat", None), "_request_binding", None)
        if binding is not None:
            binding.set_active(False)
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
        sessions = getattr(self, "_sessions", None)
        if sessions is not None:
            sessions.close()
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
        self._workspace.apply_theme(self._theme)
        body = getattr(self, "_body", None)
        if body is not None:
            self._theme.apply_surface(body)
        self._session_list.apply_theme(self._theme)
        self._chat.apply_theme(self._theme)
        self._task_monitor.apply_theme(self._theme)

    def _current_session_id(self) -> str | None:
        return self._active_session_id

    def _create_authoritative_session(self, name: str) -> None:
        return self._sessions.create(name)

    def _persist_authoritative_chat(self) -> bool:
        return self._sessions.persist()

    def _show_session_result(self, result, title: str) -> bool:
        if result.is_success:
            return True
        message = "\n".join(f"{item.code}: {item.message}" for item in result.diagnostics) or "操作失败，请重试。"
        QMessageBox.warning(self, title, message)
        return False

    def _on_session_projection(self, snapshot) -> None:
        return self._sessions.projection(snapshot)

    # ── 公共访问 ──────────────────────────────────────────────

    @property
    def chat(self) -> ChatWidget:
        return self._chat
