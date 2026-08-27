from __future__ import annotations

from collections.abc import Callable
import logging

logger = logging.getLogger(__name__)


class SessionBinding:
    """Binds the legacy session store to chat state through explicit ports."""

    def __init__(
        self,
        *,
        conversation,
        abort: Callable[[], None],
        clear_messages: Callable[[], None],
        load_history: Callable[[list[dict]], None],
        reset_task_monitor: Callable[[], None],
    ) -> None:
        self._conversation = conversation
        self._abort = abort
        self._clear_messages = clear_messages
        self._load_history = load_history
        self._reset_task_monitor = reset_task_monitor
        self._manager = None
        self._active_session_id: Callable[[], str | None] = lambda: None
        self._refresh_sessions: Callable[[], None] = lambda: None
        self._closed = False
        self._generation = 0

    def configure(
        self,
        manager,
        *,
        active_session_id: Callable[[], str | None] | None = None,
        refresh_sessions: Callable[[], None] | None = None,
    ) -> None:
        if self._closed:
            return
        self._manager = manager
        if active_session_id is not None:
            self._active_session_id = active_session_id
        if refresh_sessions is not None:
            self._refresh_sessions = refresh_sessions

    def save(self, session_id: str) -> None:
        if self._closed or self._manager is None:
            return
        messages = self._conversation.to_dict()["messages"]
        if messages:
            self._manager.save_session(session_id, messages)

    def load(self, data: dict) -> None:
        if self._closed:
            return
        self._generation += 1
        self._abort()
        self._clear_messages()
        messages = list(data.get("messages", []))
        self._conversation.from_dict({"messages": messages})
        self._load_history(messages)
        self._reset_task_monitor()

    def auto_save(self, parsed: dict | None = None) -> None:
        if self._closed or self._manager is None:
            return
        session_id = self._active_session_id()
        if not session_id:
            return
        self.save(session_id)
        if parsed:
            self._auto_name(session_id, parsed)

    def log_memory(self, memory_store, messages: list, response: str) -> None:
        if self._closed:
            return
        try:
            from transbridge.smart_assistant.memory import MemoryEntry

            user_messages = [m["content"] for m in messages if m.get("role") == "user"]
            last_user = user_messages[-1][:100] if user_messages else ""
            memory_store.add(
                MemoryEntry(
                    type="conversation",
                    summary=last_user,
                    content=f"User: {last_user}\nAssistant: {response[:300]}",
                    source="chat",
                )
            )
        except Exception as error:
            logger.warning("记忆记录失败: %s", error)

    def _auto_name(self, session_id: str, parsed: dict) -> None:
        data = self._manager.get_session(session_id)
        if data is None or data.get("name") != "新对话":
            return
        candidate = parsed.get("summary", "") or parsed.get("thought", "")
        if not candidate:
            for message in data.get("messages", []):
                content = message.get("content", "")
                if message.get("role") == "user" and not content.startswith("【"):
                    candidate = content
                    break
        name = candidate[:20].strip()
        if name:
            self._manager.rename_session(session_id, name)
            self._refresh_sessions()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._manager = None
        self._active_session_id = lambda: None
        self._refresh_sessions = lambda: None


__all__ = ["SessionBinding"]


class ConversationBinding:
    """Coordinates one LLM round without owning controller or domain state."""

    def __init__(
        self,
        *,
        memory_store,
        memory_retriever,
        controller,
        task_binding,
        session_binding,
        system_message,
        observability_visible,
    ) -> None:
        self._memory_store = memory_store
        self._memory_retriever = memory_retriever
        self._controller = controller
        self._task_binding = task_binding
        self._session_binding = session_binding
        self._system_message = system_message
        self._observability_visible = observability_visible
        self.pending_memory_context = ""
        self._closed = False

    def start_round(self, text: str) -> None:
        if self._closed:
            return
        self.pending_memory_context = ""
        if self._memory_store.count > 0:
            try:
                memories = self._memory_retriever.retrieve(text, top_k=3)
                if memories:
                    lines = ["相关历史记忆:"]
                    lines.extend(f"  - [{memory.type}] {memory.summary}" for memory in memories)
                    self.pending_memory_context = "\n".join(lines)
            except Exception as error:
                logger.info("记忆检索失败: %s", error)
        self._task_binding().start()
        controller = self._controller()
        controller.handle_abort()
        controller.handle_user_message(text)

    def handle_response(self, parsed: dict) -> None:
        if self._closed:
            return
        try:
            self._controller().handle_llm_response(parsed)
        except Exception:
            logger.error("SessionController.handle_llm_response 异常", exc_info=True)
            self._system_message("内部错误：响应处理失败，请重试")
        try:
            self._session_binding().auto_save(parsed)
        except Exception:
            logger.error("SessionBinding.auto_save 异常", exc_info=True)

    def handle_token_stats(self, stats) -> None:
        if self._closed:
            return
        if self._observability_visible() and hasattr(stats, "input_tokens"):
            self._system_message(f"Token: 输入 {stats.input_tokens} / 输出 {stats.output_tokens}")

    def clear_pending_memory(self) -> None:
        self.pending_memory_context = ""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.pending_memory_context = ""


__all__.append("ConversationBinding")
