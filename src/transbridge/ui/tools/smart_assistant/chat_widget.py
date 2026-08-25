"""Public chat facade and feature composition root."""

from __future__ import annotations

# TODO: i18n — 所有用户可见字符串均为硬编码中文，待国际化改造
import logging

from PyQt6.QtCore import QEvent, QSettings, Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QLabel, QPushButton, QScrollArea, QTextEdit, QVBoxLayout, QWidget

from transbridge.smart_assistant.conversation_manager import ConversationManager

from .chat_composition import initialize_message_area, initialize_runtime
from .confirmation_view import ConfirmationView, PlanExecutionBinding
from .input_view import ChatInputActions, ChatInputView, UploadBinding
from .lifecycle_binding import close_runtime_resources
from .message_bubble import MessageBubble
from .message_list_view import MessageListView
from .plan_card import PlanCard
from .session_binding import ConversationBinding, SessionBinding
from .streaming_presenter import StreamingPresenter
from .task_binding import TaskBinding
from .theme_support import SmartAssistantTheme
from .tool_card import BatchToolCard, ToolCard

logger = logging.getLogger(__name__)


class ChatWidget(QWidget):
    """聊天区域：消息滚动列表 + 输入框 + 发送/清空按钮 + 双模式循环控制。"""

    MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
    MAX_VISIBLE_WIDGETS = 100  # M52: 消息区最大控件数，超出时从头部移除最旧控件

    def __init__(self, ctx, parent=None, *, theme: SmartAssistantTheme | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self._theme = theme or SmartAssistantTheme()
        self.setAccessibleName("智能助手聊天")

        # 全局字体
        from PyQt6.QtGui import QFont

        font = QFont("Microsoft YaHei", 10)
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)

        self._conversation = ConversationManager(max_turns=20)
        self._uploaded_docs: dict[str, object] = {}  # filename → ParsedDocument
        self._upload_binding = UploadBinding(
            parent=self,
            documents=self._uploaded_docs,
            notify=self.add_system_message,
            max_bytes=self.MAX_UPLOAD_BYTES,
        )

        # m2: 重试按钮引用，防止多次错误叠加多个重试按钮
        self._retry_btn: QPushButton | None = None

        # FR14: 后台任务监控
        self._task_monitor = None

        # 自动模式 (QSettings 持久化)
        self._auto_mode = False
        try:
            qs = QSettings("TransBridge", "SmartAssistant")
            self._auto_mode = qs.value("auto_mode", False, type=bool)
        except Exception as e:
            logger.debug("QSettings auto_mode 读取失败，使用默认值 False: %s", e)

        # 可观测性收集器引用（延迟创建）
        self._obs_collector = None
        # 长期记忆引用（延迟创建）
        self._memory_store = None
        self._memory_retriever = None
        # UI 控件引用（延迟创建，resizeEvent 等可能在 _init_ui 前触发）
        self._main_layout: QVBoxLayout | None = None
        self._back_to_bottom_btn: QPushButton | None = None
        self._scroll: QScrollArea | None = None
        self._input: QTextEdit | None = None
        self._input_view: ChatInputView | None = None
        self._input_actions: ChatInputActions | None = None
        self._message_list: MessageListView | None = None
        self._streaming_presenter: StreamingPresenter | None = None
        self._confirmation_view: ConfirmationView | None = None
        self._plan_execution: PlanExecutionBinding | None = None
        self._session_binding: SessionBinding | None = None
        self._conversation_binding: ConversationBinding | None = None
        self._task_binding: TaskBinding | None = None
        self._session_mgr = None
        self._active_session_id_port = lambda: None
        self._refresh_sessions_port = lambda: None

        # 延后 UI 构建：__init__ 累积的 Python→C++ 调用在 Windows 1MB C 栈
        # 上可能溢出 (0xC00000FD)。通过 QTimer.singleShot 将 QObject 密集的
        # UI 构建推迟到事件循环空闲时，此时 C 栈已完全展开。
        QTimer.singleShot(0, self._init_ui)

    def _init_ui(self) -> None:
        """延迟构建 UI 入口：分 4 阶段串行化，每阶段间 C 栈完全展开。

        从 __init__ 通过 QTimer.singleShot(0) 调用。每阶段结束调度下一阶段，
        避免单次调用帧内累积过多 Python→C++ QObject 创建导致 C 栈溢出。
        """
        if getattr(self, "_shutdown_complete", False):
            return
        self._init_ui_stage1()

    def _init_ui_stage1(self) -> None:
        """Stage 1/4: QTimers + 长期记忆 + 可观测性收集器。"""
        if getattr(self, "_shutdown_complete", False):
            return
        try:
            initialize_runtime(self)

        except Exception as e:
            logger.error("UI初始化 Stage 1/4 失败: %s", e)

        QTimer.singleShot(0, self._init_ui_stage2)

    def _init_ui_stage2(self) -> None:
        """Stage 2/4: 布局 + 消息滚动区 + 回到底部按钮。"""
        if getattr(self, "_shutdown_complete", False):
            return
        try:
            initialize_message_area(self)
        except Exception as e:
            logger.error("UI初始化 Stage 2/4 失败: %s", e)

        QTimer.singleShot(0, self._init_ui_stage3)

    def _init_ui_stage3(self) -> None:
        """Stage 3/4: 工具栏(chips+上传) + 观测面板(QTabWidget/QTableWidget)。"""
        if getattr(self, "_shutdown_complete", False):
            return
        try:
            assert self._main_layout is not None
            self._input_actions = ChatInputActions(
                chat_facade=self,
                orchestrator=self._orchestrator,
                controller=self._controller,
                auto_mode=self._auto_mode,
                notify=self.add_system_message,
            )
            self._input_view = ChatInputView(
                set_input=self.set_input,
                select_skill=self._input_actions.select_skill,
                upload=lambda: self._upload_binding.select_files(self._upload_label),
                clear=self._clear_conversation,
                send=self._on_send,
                toggle_auto=self._input_actions.toggle_auto,
                auto_mode=self._auto_mode,
                theme=self._theme,
            )
            self._input_view.build_toolbar(self._main_layout)
            self._upload_label = self._input_view.upload_label
            self._obs_token_labels: dict[str, QLabel] = {}
        except Exception as e:
            logger.error("UI初始化 Stage 3/4 失败: %s", e)

        QTimer.singleShot(0, self._init_ui_stage4)

    def _init_ui_stage4(self) -> None:
        """Stage 4/4: 输入框 + 按钮行 + 异步通知调度。"""
        if getattr(self, "_shutdown_complete", False):
            return
        try:
            assert self._main_layout is not None
            assert self._input_view is not None
            self._input_view.build_editor(self._main_layout, self)
            self._input = self._input_view.input
            self._send_btn = self._input_view.send_button
            self._auto_cb = self._input_view.auto_checkbox

            # ── Ctrl+O 快捷键：展开/折叠思考过程 ──
            self._shortcut_ctrl_o = QShortcut(QKeySequence("Ctrl+O"), self)
            self._shortcut_ctrl_o.activated.connect(self._message_list.toggle_thinking)
        except Exception as e:
            logger.error("UI初始化 Stage 4/4 失败: %s", e)

    # ── 公共方法 ──────────────────────────────────────────────

    def shutdown(self, *, wait_for_worker: bool = True) -> None:
        """关闭 ChatWidget 时清理所有资源（B8: panel.closeEvent 只需调用此方法）。

        所有清理操作各自 try/except，确保单个失败不影响后续清理。
        """
        if getattr(self, "_shutdown_complete", False):
            return
        self._shutdown_complete = True
        # 1/ 先关闭 UI-owned bindings/presenters，迟到事件从此被忽略。
        for attr in (
            "_confirmation_view",
            "_plan_execution",
            "_input_view",
            "_upload_binding",
            "_streaming_presenter",
            "_session_binding",
            "_conversation_binding",
            "_task_binding",
            "_message_list",
        ):
            try:
                owner = getattr(self, attr, None)
                if owner is not None:
                    owner.close()
            except Exception:
                logger.debug("shutdown: 关闭 %s 失败", attr, exc_info=True)

        close_runtime_resources(
            observability=getattr(self, "_obs_collector", None),
            memory_store=getattr(self, "_memory_store", None),
            orchestrator=getattr(self, "_orchestrator", None),
            wait_for_worker=wait_for_worker,
        )

    def set_input(self, text: str) -> None:
        if self._input_view is not None:
            self._input_view.set_text(text)

    def apply_theme(self, theme: SmartAssistantTheme) -> None:
        """Refresh presentation only; conversation, streaming and task state stay authoritative."""
        self._theme = theme
        theme.apply_surface(self)
        if self._scroll is not None:
            theme.apply_surface(self._scroll)
        msg_container = getattr(self, "_msg_container", None)
        if msg_container is not None:
            theme.apply_surface(msg_container)
        if self._message_list is not None:
            self._message_list.apply_theme(theme)
        if self._input_view is not None:
            self._input_view.apply_theme(theme)
        if self._confirmation_view is not None:
            self._confirmation_view.apply_theme(theme)
        if self._back_to_bottom_btn is not None:
            theme.apply_semantic(self._back_to_bottom_btn, "primary", background=True)

    def _presentation_theme(self) -> SmartAssistantTheme:
        """Compatibility fallback for legacy tests/facades created without __init__."""
        theme = getattr(self, "_theme", None)
        if theme is None:
            theme = SmartAssistantTheme()
            self._theme = theme
        return theme

    @property
    def context(self):
        """Compatibility read port for Panel-level project naming."""
        return self._ctx

    def recovery_snapshot(self) -> tuple[list[dict], dict]:
        """Return authoritative conversation and controller recovery data."""
        backend = list(self._conversation.to_dict().get("messages", []))
        controller = self._controller.to_recovery_snapshot()
        return backend, controller

    def add_system_prompt(self, text: str) -> None:
        """注入 System Prompt 到对话历史（供 SkillExecutor 等外部调用者使用）。

        将文本作为 system 角色消息插入 conversation，替换已有 system 消息。
        注意：此方法仅操作数据层 (_conversation)，不产生 UI 渲染。
        """
        self._conversation.add_system(text)

    def send_user_message(self, text: str) -> None:
        """以编程方式发送用户消息并触发 LLM 推理（供 SkillExecutor 等外部调用者使用）。

        封装了完整发送流程：添加用户气泡 → 写入对话历史 → 触发 LLM 轮次。
        与 _on_send() 不同，此方法直接接受文本参数，不依赖 _input 控件状态。
        """
        text = text.strip()
        if not text:
            return
        self._orchestrator.cancel_current_round()
        if self._message_list is not None:
            self._message_list.add_bubble(MessageBubble(text, "user", theme=self._presentation_theme()))
        self._conversation.add_user(text)
        # m22: LLM 推理在后台 QThread 中异步执行，本方法立即返回
        QTimer.singleShot(
            0,
            lambda: self._conversation_binding.start_round(text) if self._conversation_binding is not None else None,
        )

    def add_user_bubble(self, text: str) -> None:
        """Add a user bubble through the stable public facade."""
        if self._message_list is None:
            if not getattr(self, "_shutdown_complete", False):
                QTimer.singleShot(50, lambda: self.add_user_bubble(text))
            return
        self._message_list.add_bubble(MessageBubble(text, "user", theme=self._presentation_theme()))

    def add_assistant_bubble(self, text: str) -> None:
        """Add an assistant bubble through the stable public facade."""
        if self._message_list is None:
            if not getattr(self, "_shutdown_complete", False):
                QTimer.singleShot(50, lambda: self.add_assistant_bubble(text))
            return
        self._message_list.add_bubble(MessageBubble(text, "assistant", theme=self._presentation_theme()))

    def add_system_message(self, text: str) -> None:
        """FR7.16: 融入式系统消息 — 轻量横条标签替代居中灰色文本。"""
        if self._message_list is None:
            if getattr(self, "_shutdown_complete", False):
                return
            QTimer.singleShot(50, lambda: self.add_system_message(text))
            return
        self._message_list.add_system_message(text)

    def add_tool_card(self, step: dict) -> ToolCard:
        if self._confirmation_view is None:
            if getattr(self, "_shutdown_complete", False):
                return None
            QTimer.singleShot(50, lambda: self.add_tool_card(step))
            return None
        return self._confirmation_view.add_tool_card(step)

    def add_plan_card(self, steps: list) -> PlanCard:
        if self._confirmation_view is None:
            if getattr(self, "_shutdown_complete", False):
                return None
            QTimer.singleShot(50, lambda: self.add_plan_card(steps))
            return None
        return self._confirmation_view.add_plan_card(steps)

    def add_batch_tool_card(self, steps: list) -> BatchToolCard:
        if self._confirmation_view is None:
            if getattr(self, "_shutdown_complete", False):
                return None
            QTimer.singleShot(50, lambda: self.add_batch_tool_card(steps))
            return None
        return self._confirmation_view.add_batch_tool_card(steps)

    def _offer_retry_button(self) -> None:
        """创建重试按钮并添加到消息区。"""
        if self._retry_btn is not None:
            if self._message_list is not None:
                self._message_list.remove(self._retry_btn)
        retry_btn = QPushButton("重试")
        retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        retry_btn.clicked.connect(self._orchestrator.retry)
        if self._message_list is not None:
            self._message_list.add_widget(retry_btn)
        self._retry_btn = retry_btn

    # ── ReAct 模式 ───────────────────────────────────────────

    def _on_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return

        # FR7.16: /obs 命令切换观测信息显示
        if text == "/obs":
            self._input.clear()
            if self._input_actions is not None:
                self._input_actions.toggle_observability()
            return

        # 中断正在进行的流式输出
        self._orchestrator.cancel_current_round()

        if self._message_list is not None:
            self._message_list.add_bubble(MessageBubble(text, "user", theme=self._presentation_theme()))
        self._input.clear()
        self._conversation.add_user(text)

        # M18: 延迟到事件循环执行检索+LLM，避免主线程同步检索阻塞 UI
        QTimer.singleShot(
            0,
            lambda: self._conversation_binding.start_round(text) if self._conversation_binding is not None else None,
        )

    def _clear_conversation(self) -> None:
        self._orchestrator.cancel_current_round()
        if self._plan_execution is not None:
            self._plan_execution.abort()
        self._controller.handle_abort()
        self._orchestrator.reset_state()
        if self._message_list is not None:
            self._message_list.hide_thinking()
        if self._streaming_presenter is not None:
            self._streaming_presenter.advance_generation()
        if self._message_list is not None:
            self._message_list.clear()
        self._conversation.clear()
        # M13联动: 释放上传文件引用
        self._uploaded_docs.clear()
        # m2联动: 清空重试按钮引用
        self._retry_btn = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_reading_width()
        if self._message_list is not None:
            self._message_list.reposition_later()

    def _update_reading_width(self) -> None:
        """Keep messages in a readable centre column on wide desktop windows."""
        scroll = self._scroll
        layout = getattr(self, "_msg_layout", None)
        if scroll is None or layout is None:
            return
        viewport_width = max(0, scroll.viewport().width())
        horizontal_margin = max(20, (viewport_width - 840) // 2)
        layout.setContentsMargins(horizontal_margin, 20, horizontal_margin, 20)

    # ── 会话持久化 (FR13 Story 03) ──────────────────────────

    def set_session_manager(self, mgr):
        """注入 SessionManager 实例（由 Panel 调用）。"""
        self._session_mgr = mgr
        if self._session_binding is not None:
            self._session_binding.configure(
                mgr,
                active_session_id=self._active_session_id_port,
                refresh_sessions=self._refresh_sessions_port,
            )

    def configure_session_port(
        self,
        *,
        active_session_id,
        refresh_sessions,
    ) -> None:
        """Inject Panel session ports without parent-chain/private lookup."""
        self._active_session_id_port = active_session_id
        self._refresh_sessions_port = refresh_sessions
        if self._session_binding is not None:
            self._session_binding.configure(
                self._session_mgr,
                active_session_id=active_session_id,
                refresh_sessions=refresh_sessions,
            )

    # ── FR14: 后台任务监控 ────────────────────────────────────

    def set_task_monitor(self, monitor) -> None:
        """注入 TaskMonitorWidget 引用（由 Panel 调用）。"""
        self._task_monitor = monitor
        if self._task_binding is not None:
            self._task_binding.set_monitor(monitor)

    def refresh_task_monitor(self) -> None:
        """Refresh the injected task monitor through its binding."""
        if self._task_binding is not None:
            self._task_binding.refresh()

    def save_current_session(self, session_id: str) -> None:
        """保存当前对话到指定会话。"""
        if self._session_binding is not None:
            self._session_binding.save(session_id)

    def load_session(self, data: dict) -> None:
        """加载会话数据：清空当前对话并渲染历史消息。"""
        if self._session_binding is None:
            if not getattr(self, "_shutdown_complete", False):
                QTimer.singleShot(50, lambda: self.load_session(dict(data)))
            return
        if self._streaming_presenter is not None:
            self._streaming_presenter.advance_generation()
        self._session_binding.load(data)

    def load_history(self, messages: list[dict]) -> None:
        """渲染历史消息列表为 MessageBubble。若 UI 尚未就绪则延迟重试。"""
        if self._message_list is None:
            if getattr(self, "_shutdown_complete", False):
                return
            QTimer.singleShot(50, lambda: self.load_history(messages))
            return
        self._message_list.load_history(messages)

    def eventFilter(self, obj, event):
        if obj == self._input and event.type() == QEvent.Type.KeyPress:
            ke = event
            if ke.key() == Qt.Key.Key_Return and ke.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._on_send()
                return True
        return super().eventFilter(obj, event)
