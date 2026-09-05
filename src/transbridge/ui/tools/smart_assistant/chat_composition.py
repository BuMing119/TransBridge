from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QScrollArea, QVBoxLayout, QWidget

from transbridge.smart_assistant.conversation_orchestrator import ConversationOrchestrator
from transbridge.smart_assistant.session_controller import SessionController
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler

from .confirmation_view import ConfirmationActions, ConfirmationView, PlanExecutionBinding
from .message_bubble import MessageBubble
from .message_list_view import MessageListView
from .react_execution_binding import ReactExecutionBinding
from .session_binding import ConversationBinding, SessionBinding
from .streaming_presenter import StreamingPresenter
from .task_binding import TaskBinding, sanitize_error_message
from .theme_support import CHIP_STRUCTURE_STYLE

logger = logging.getLogger(__name__)


def initialize_runtime(facade) -> None:
    """Compose backend adapters; the facade remains the public compatibility root."""
    from transbridge.config.paths import get_data_dir
    from transbridge.smart_assistant.memory import MemoryRetriever, MemoryStore

    facade._memory_store = MemoryStore(
        Path(get_data_dir()) / "memory",
        embedding_mode="disabled",
        persist_to_disk=False,
    )
    facade._memory_retriever = MemoryRetriever(facade._memory_store)

    from transbridge.smart_assistant.observability import ObservabilityCollector

    facade._obs_collector = ObservabilityCollector(
        storage_dir=Path(get_data_dir()) / "observability",
        on_token_stats_updated=lambda stats: (
            facade._conversation_binding.handle_token_stats(stats) if facade._conversation_binding is not None else None
        ),
    )
    facade._tool_handler = ToolExecutionHandler(
        ctx=facade._ctx,
        conversation_manager=facade._conversation,
        on_system_message=facade.add_system_message,
        on_plan_card=facade.add_plan_card,
        on_tool_card=facade.add_tool_card,
        on_batch_tool_card=facade.add_batch_tool_card,
        on_plan_confirmed=lambda steps: (
            facade._plan_execution.confirm(steps, facade._confirmation_view)
            if facade._plan_execution is not None and facade._confirmation_view is not None
            else None
        ),
        on_step_completed=lambda: facade._controller.handle_execution_complete([]),
        on_task_started=lambda task_id, run_id: facade._controller.handle_task_started(task_id, run_id),
        on_confirm_permission=lambda title, message: (
            facade._confirmation_view.ask_permission(title, message) if facade._confirmation_view is not None else False
        ),
        llm_client_provider=lambda: facade._orchestrator.get_llm_client(),
    )
    facade._orchestrator = ConversationOrchestrator(
        ctx=facade._ctx,
        conversation_manager=facade._conversation,
        tool_execution_handler=facade._tool_handler,
        obs_collector=facade._obs_collector,
        memory_store=facade._memory_store,
        on_system_message=facade.add_system_message,
        on_streaming_bubble_factory=lambda: MessageBubble("...", "assistant", theme=facade._theme),
        on_streaming_flush=lambda text, bubble, dirty: (
            facade._streaming_presenter.flush(text, bubble) if facade._streaming_presenter is not None else None
        ),
        on_add_bubble=lambda bubble: (
            facade._message_list.add_bubble(bubble) if facade._message_list is not None else None
        ),
        on_scroll_to_bottom=lambda: (
            facade._message_list.scroll_to_bottom() if facade._message_list is not None else None
        ),
        on_thinking_indicator_show=lambda thought: (
            facade._message_list.show_thinking(thought) if facade._message_list is not None else None
        ),
        on_thinking_indicator_hide=lambda: (
            facade._message_list.hide_thinking() if facade._message_list is not None else None
        ),
        on_plan_card=facade.add_plan_card,
        on_tool_card=facade.add_tool_card,
        on_batch_tool_card=facade.add_batch_tool_card,
        on_end_conversation=facade._obs_collector.end_conversation,
        on_remove_widget=lambda widget: (
            facade._message_list.remove(widget) if facade._message_list is not None else None
        ),
        on_retry_offer=lambda message: facade._offer_retry_button(),
        on_log_memory=lambda messages, response: (
            facade._session_binding.log_memory(facade._memory_store, messages, response)
            if facade._session_binding is not None
            else None
        ),
        on_get_uploaded_docs=lambda: facade._uploaded_docs,
        on_get_pending_memory=lambda: (
            facade._conversation_binding.pending_memory_context if facade._conversation_binding is not None else ""
        ),
        on_clear_pending_memory=lambda: (
            facade._conversation_binding.clear_pending_memory() if facade._conversation_binding is not None else None
        ),
        on_response_parsed=lambda parsed: (
            facade._conversation_binding.handle_response(parsed) if facade._conversation_binding is not None else None
        ),
    )
    facade._orchestrator.auto_mode = facade._auto_mode
    facade._react_execution = ReactExecutionBinding(
        parent=facade,
        handler=facade._tool_handler,
        controller=lambda: facade._controller,
        system_message=facade.add_system_message,
    )
    facade._controller = SessionController(
        orchestrator=facade._orchestrator,
        tool_handler=facade._tool_handler,
        conversation=facade._conversation,
        on_state_changed=lambda old, new, ctx: logger.debug("SessionController: %s → %s", old.value, new.value),
        on_present_plan_card=facade.add_plan_card,
        on_present_tool_card=facade.add_tool_card,
        on_present_batch_tool_card=facade.add_batch_tool_card,
        on_system_message=facade.add_system_message,
        on_conversation_end=facade._obs_collector.end_conversation,
        on_llm_round_start=facade._orchestrator.start_round,
        on_thinking_indicator_hide=lambda: (
            facade._message_list.hide_thinking() if facade._message_list is not None else None
        ),
        on_execute_react_async=facade._react_execution.execute,
    )
    facade._controller.auto_mode = facade._auto_mode


def initialize_message_area(facade) -> None:
    """Compose the message View, presenters and bindings."""
    facade._main_layout = QVBoxLayout(facade)
    facade._main_layout.setContentsMargins(14, 12, 14, 12)
    facade._main_layout.setSpacing(12)
    facade._scroll = QScrollArea()
    facade._scroll.setAccessibleName("消息滚动区域")
    facade._scroll.setWidgetResizable(True)
    facade._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    facade._msg_container = QWidget()
    facade._msg_layout = QVBoxLayout(facade._msg_container)
    facade._msg_layout.setContentsMargins(20, 20, 20, 20)
    facade._msg_layout.setSpacing(6)
    facade._msg_layout.addStretch()
    facade._scroll.setWidget(facade._msg_container)
    facade._main_layout.addWidget(facade._scroll, stretch=1)
    facade._theme.apply_surface(facade)
    facade._theme.apply_surface(facade._scroll)
    facade._theme.apply_surface(facade._msg_container)
    facade._back_to_bottom_btn = QPushButton("回到底部", facade._scroll)
    facade._back_to_bottom_btn.setAccessibleName("回到消息底部")
    facade._back_to_bottom_btn.setStyleSheet(CHIP_STRUCTURE_STYLE)
    facade._theme.apply_semantic(facade._back_to_bottom_btn, "primary", background=True)
    facade._back_to_bottom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    facade._back_to_bottom_btn.setVisible(False)
    facade._back_to_bottom_btn.raise_()
    facade._message_list = MessageListView(
        facade._msg_layout,
        scroll_area=facade._scroll,
        back_to_bottom_button=facade._back_to_bottom_btn,
        timer_parent=facade,
        max_visible_widgets=facade.MAX_VISIBLE_WIDGETS,
        theme=facade._theme,
    )
    facade._update_reading_width()
    facade._streaming_presenter = StreamingPresenter(facade._message_list)
    facade._plan_execution = PlanExecutionBinding(
        context=facade._ctx,
        controller=lambda: facade._controller,
        middlewares=facade._tool_handler.ensure_middlewares,
        observability=facade._obs_collector,
        conversation=facade._conversation,
        hide_thinking=facade._message_list.hide_thinking,
        system_message=facade.add_system_message,
        retry_handler=lambda: facade._tool_handler.retry_handler,
    )
    facade._confirmation_actions = ConfirmationActions(
        controller=lambda: facade._controller,
        conversation=facade._conversation,
        hide_thinking=facade._message_list.hide_thinking,
        system_message=facade.add_system_message,
    )
    facade._confirmation_view = ConfirmationView(
        parent=facade,
        add_widget=facade._message_list.add_widget,
        plan_confirmed=lambda steps: facade._plan_execution.confirm(steps, facade._confirmation_view),
        plan_cancelled=facade._plan_execution.cancel,
        tool_executed=facade._confirmation_actions.execute_tool,
        tool_ignored=facade._confirmation_actions.ignore_tool,
        batch_executed=facade._confirmation_actions.execute_batch,
        batch_ignored=facade._confirmation_actions.ignore_batch,
        engine=lambda: facade._plan_execution.engine,
        theme=facade._theme,
    )

    def abort_session_activity() -> None:
        facade._plan_execution.abort()
        facade._react_execution.abort()
        facade._controller.handle_abort()

    facade._session_binding = SessionBinding(
        conversation=facade._conversation,
        abort=abort_session_activity,
        clear_messages=facade._message_list.clear,
        load_history=facade._message_list.load_history,
        reset_task_monitor=lambda: facade._task_binding.reset_monitor() if facade._task_binding is not None else None,
        restore_controller=facade._restore_session_controller,
    )
    facade._session_binding.configure(
        facade._session_mgr,
        active_session_id=facade._active_session_id_port,
        refresh_sessions=facade._refresh_sessions_port,
        save_session=facade._save_session_port,
    )
    facade._task_binding = TaskBinding(
        parent=facade,
        conversation=facade._conversation,
        system_message=facade.add_system_message,
        controller=lambda: facade._controller,
        sanitize_error=sanitize_error_message,
    )
    if facade._task_monitor is not None:
        facade._task_binding.set_monitor(facade._task_monitor)
    facade._conversation_binding = ConversationBinding(
        memory_store=facade._memory_store,
        memory_retriever=facade._memory_retriever,
        controller=lambda: facade._controller,
        task_binding=lambda: facade._task_binding,
        session_binding=lambda: facade._session_binding,
        system_message=facade.add_system_message,
        observability_visible=lambda: (
            facade._input_actions.observability_visible if facade._input_actions is not None else False
        ),
    )


__all__ = ["initialize_message_area", "initialize_runtime"]
