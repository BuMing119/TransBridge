"""ConversationOrchestrator — LLM 轮次编排与流式管理。

从 ChatWidget 提取，遵循 ADR-008 代码分层。
通过回调与 UI 层通信，不持有 ChatWidget 引用。
"""
import logging
import os
import re
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

logger = logging.getLogger(__name__)


def _create_llm_client(config, _cache: dict | None = None):
    """Create or reuse an LLM client based on configuration.

    Extracted from ConversationOrchestrator._get_llm_client() for reuse and
    testability. Computes a cache key from config attributes, checks the mutable
    _cache dict for a matching client, and only creates a new client when the
    configuration has changed.

    Args:
        config: LLMConfig object with api_key, provider, base_url, model.
        _cache: Optional mutable dict for client caching. Keys:
            "client" — cached LLMClient instance (or None).
            "config_hash" — hash of current (api_key, provider, base_url, model).

    Returns:
        An LLMClient instance, or None if config is None or missing an api_key.
    """
    from src.transbridge.infra.llm_client import create_llm_client

    if config is None or not config.api_key:
        return None

    current_hash = hash((config.api_key, config.provider, config.base_url, config.model))

    if _cache is not None:
        cached = _cache.get("client")
        cached_hash = _cache.get("config_hash")
        if cached is not None and cached_hash == current_hash:
            return cached

    client = create_llm_client(config)

    if _cache is not None:
        _cache["client"] = client
        _cache["config_hash"] = current_hash

    return client


class _SignalBridge(QObject):
    """Worker→主线程回调桥接器。

    在 worker 线程中调用 _dispatch.emit(callback)，Qt 自动排队到主线程执行。
    """
    _dispatch = pyqtSignal(object)


class ConversationOrchestrator(QObject):
    """LLM 对话编排：轮次管理、流式处理、Worker 生命周期、模式分发。

    所有 UI 操作通过回调注入，不持有任何 QWidget 引用（_SignalBridge 除外）。
    """

    _STREAMING_FLUSH_MS = 50
    _MAX_STREAMING_CHARS = 50000

    def __init__(
        self,
        ctx,
        conversation_manager,
        tool_execution_handler,
        obs_collector=None,
        memory_store=None,
        *,
        # UI 回调
        on_system_message: Callable[[str], None] | None = None,
        on_streaming_bubble_factory: Callable[[], Any] | None = None,
        on_streaming_flush: Callable[[str, Any, bool], None] | None = None,
        on_add_bubble: Callable[[Any], None] | None = None,
        on_scroll_to_bottom: Callable[[], None] | None = None,
        # 响应分发回调
        on_thinking_indicator_show: Callable[[str], None] | None = None,
        on_thinking_indicator_hide: Callable[[], None] | None = None,
        on_auto_execute_steps: Callable[[list, str], None] | None = None,
        on_plan_card: Callable[[list], None] | None = None,
        on_tool_card: Callable[[dict], None] | None = None,
        on_batch_tool_card: Callable[[list], None] | None = None,
        on_end_conversation: Callable[[], None] | None = None,
        on_remove_widget: Callable[[Any], None] | None = None,
        # 错误回调
        on_retry_offer: Callable[[str], None] | None = None,
        # 记忆回调
        on_log_memory: Callable[[list, str], None] | None = None,
        # 上下文回调
        on_get_uploaded_docs: Callable[[], dict] | None = None,
        on_get_pending_memory: Callable[[], str] | None = None,
        on_clear_pending_memory: Callable[[], None] | None = None,
        # React 深度回调
        on_react_depth_check: Callable[[], bool] | None = None,
        # FR12: SessionController 响应回调
        on_response_parsed: Callable[[dict], None] | None = None,
    ):
        super().__init__(None)
        self._ctx = ctx
        self._conversation = conversation_manager
        self._tool_handler = tool_execution_handler
        self._obs_collector = obs_collector
        self._memory_store = memory_store

        # LLM client cache
        self._prompt_builder = None
        self._llm_client = None
        self._llm_client_config_hash = None
        self._llm_config_mtime = None
        self._cached_llm_config = None

        # Worker
        self._worker = None
        self._react_depth = 0
        self._consecutive_errors = 0

        # Micro-stage temp state
        self._round_messages: list = []
        self._round_max_tokens: int = 0

        # Streaming state
        self._streaming_text = ""
        self._streaming_bubble: Any = None
        self._streaming_dirty = False
        self._auto_mode = False

        # Signal bridge
        self._cb_bridge = _SignalBridge()
        self._cb_bridge._dispatch.connect(lambda cb: cb())

        # Streaming timer
        self._streaming_timer = QTimer()
        self._streaming_timer.setInterval(self._STREAMING_FLUSH_MS)
        self._streaming_timer.timeout.connect(self._flush_streaming)

        # Callbacks
        self._on_system_message = on_system_message or (lambda _: None)
        self._on_streaming_bubble_factory = on_streaming_bubble_factory or (lambda: None)
        self._on_streaming_flush = on_streaming_flush or (lambda *a: None)
        self._on_add_bubble = on_add_bubble or (lambda _: None)
        self._on_scroll_to_bottom = on_scroll_to_bottom or (lambda: None)
        self._on_thinking_indicator_show = on_thinking_indicator_show or (lambda _: None)
        self._on_thinking_indicator_hide = on_thinking_indicator_hide or (lambda: None)
        self._on_auto_execute_steps = on_auto_execute_steps or (lambda *a: None)
        self._on_plan_card = on_plan_card or (lambda _: None)
        self._on_tool_card = on_tool_card or (lambda _: None)
        self._on_batch_tool_card = on_batch_tool_card or (lambda _: None)
        self._on_end_conversation = on_end_conversation or (lambda: None)
        self._on_remove_widget = on_remove_widget or (lambda _: None)
        self._on_retry_offer = on_retry_offer or (lambda _: None)
        self._on_log_memory = on_log_memory or (lambda *a: None)
        self._on_get_uploaded_docs = on_get_uploaded_docs or (lambda: {})
        self._on_get_pending_memory = on_get_pending_memory or (lambda: "")
        self._on_clear_pending_memory = on_clear_pending_memory or (lambda: None)
        self._on_react_depth_check = on_react_depth_check or (lambda: True)
        self._on_response_parsed = on_response_parsed or (lambda _: None)  # FR12

    # ── 客户端管理 ─────────────────────────────────────────

    def _get_prompt_builder(self):
        if self._prompt_builder is None:
            from src.transbridge.ai_translator.prompt_builder import PromptBuilder
            self._prompt_builder = PromptBuilder()
        return self._prompt_builder

    def _get_llm_client(self):
        from src.transbridge.paratranz.config_manager import LLMConfig
        from src.transbridge.config.paths import get_config_file_path

        config_path = get_config_file_path()

        # M49: Only reload config from disk if the file has been modified
        try:
            current_mtime = os.path.getmtime(config_path)
        except OSError:
            current_mtime = 0

        if self._llm_config_mtime is None or current_mtime != self._llm_config_mtime:
            cfg = LLMConfig.load_from_file()
            self._llm_config_mtime = current_mtime
            self._cached_llm_config = cfg
        else:
            cfg = self._cached_llm_config

        if not cfg.api_key:
            return None

        cache = {
            "client": self._llm_client,
            "config_hash": self._llm_client_config_hash,
        }
        client = _create_llm_client(cfg, cache)
        self._llm_client = cache["client"]
        self._llm_client_config_hash = cache["config_hash"]
        return client

    # ── Worker 访问（供 panel.closeEvent 使用）──

    @property
    def worker(self):
        return self._worker

    # ── LLM 轮次 ───────────────────────────────────────────

    def start_round(self) -> None:
        """Stage A: 准备上下文，构建系统提示词。"""
        # Guard: cancel any running worker to prevent concurrent rounds
        if self._worker is not None:
            self.cancel_current_round()

        client = self._get_llm_client()
        if client is None:
            self._on_system_message("请先在设置中配置 LLM API Key")
            return

        if not any(m["role"] == "system" for m in self._conversation.get_messages()):
            from src.transbridge.smart_assistant.context_builder import ContextBuilder
            from src.transbridge.smart_assistant.prompts import build_system_prompt
            ctx = self._ctx
            ctx._uploaded_docs = self._on_get_uploaded_docs()
            context = ContextBuilder(ctx).build()
            sys_prompt = build_system_prompt(context)
            pending = self._on_get_pending_memory()
            if pending:
                sys_prompt = sys_prompt + "\n\n" + pending
                self._on_clear_pending_memory()
            self._conversation.add_system(sys_prompt)

        self._react_depth += 1
        self._on_thinking_indicator_hide()
        self._round_messages = self._conversation.get_messages()
        self._round_max_tokens = 0
        QTimer.singleShot(0, self._stage_b)

    def _stage_b(self) -> None:
        """Stage B: 创建流式气泡。"""
        self._streaming_text = ""
        bubble = self._on_streaming_bubble_factory()
        if bubble is not None:
            self._streaming_bubble = bubble
            self._on_add_bubble(bubble)
        QTimer.singleShot(0, self._stage_c)

    def _stage_c(self) -> None:
        """Stage C: 创建 ChatWorker，绑定回调，启动后台线程。"""
        from src.transbridge.smart_assistant.chat_worker import ChatWorker
        client = self._get_llm_client()
        messages = getattr(self, '_round_messages', [])
        _max = getattr(self, '_round_max_tokens', 0)
        self._round_messages = []
        self._round_max_tokens = 0

        _bridge = self._cb_bridge

        self._worker = ChatWorker(client, messages, max_tokens=_max)
        self._worker.on_chunk = lambda c: _bridge._dispatch.emit(
            lambda: self._on_chunk(c))
        self._worker.on_finished = lambda t: _bridge._dispatch.emit(
            lambda: self._on_finished(t))
        self._worker.on_error = lambda m: _bridge._dispatch.emit(
            lambda: self._on_error(m))
        if self._obs_collector:
            self._worker.on_token_usage = lambda model, i, o: _bridge._dispatch.emit(
                lambda: self._obs_collector.on_llm_tokens(model, i, o))
        self._worker.start()

    # ── 流式处理 ───────────────────────────────────────────

    def _on_chunk(self, chunk: str) -> None:
        # M57: Guard against unbounded memory growth on very long responses
        if len(self._streaming_text) >= self._MAX_STREAMING_CHARS:
            return
        self._streaming_text += chunk
        self._streaming_dirty = True
        if not self._streaming_timer.isActive():
            self._streaming_timer.start()

    def _flush_streaming(self) -> None:
        if not self._streaming_dirty or self._streaming_bubble is None:
            self._streaming_timer.stop()
            return
        self._streaming_dirty = False
        self._on_streaming_flush(self._streaming_text, self._streaming_bubble, True)
        self._on_scroll_to_bottom()

    # ── 响应处理 ───────────────────────────────────────────

    def _on_finished(self, response: str) -> None:
        self._streaming_timer.stop()
        _finished_bubble = self._streaming_bubble
        if self._streaming_bubble:
            self._on_streaming_flush(self._streaming_text, self._streaming_bubble, False)
            self._streaming_bubble.set_text(self._streaming_text)
            self._streaming_bubble = None
        self._streaming_text = ""

        self._consecutive_errors = 0
        pb = self._get_prompt_builder()
        parsed = pb.parse_hybrid_response(response)

        thought = parsed.get("thought", "")
        steps = parsed.get("steps", [])
        mode = parsed.get("mode", "react")

        self._conversation.add_assistant(response)

        # FR7.16: 先清掉流式气泡（用户不应看到 JSON），再通知 Controller 分发
        if thought and steps:
            if _finished_bubble is not None:
                self._on_remove_widget(_finished_bubble)
            self._on_thinking_indicator_show(thought)

        # FR12 Story 02: 分发逻辑移交给 SessionController
        self._on_response_parsed(parsed)

        if not steps:
            self._on_thinking_indicator_hide()
            self._on_end_conversation()

        # 记录记忆
        self._on_log_memory(
            self._conversation.get_messages(),
            response[:300],
        )

        # 清理 worker
        self._cleanup_worker()

    def _on_error(self, msg: str) -> None:
        self._streaming_timer.stop()
        self._streaming_text = ""
        self._streaming_dirty = False
        self._react_depth = 0
        self._consecutive_errors += 1

        # M68: 脱敏错误消息，移除文件路径和 API 密钥
        safe_msg = self._sanitize_error_message(msg)

        # 清理流式气泡
        if self._streaming_bubble:
            self._on_remove_widget(self._streaming_bubble)
            self._streaming_bubble = None

        is_network = any(kw in msg.lower() for kw in (
            "timeout", "connection", "refused", "network", "reset", "unreachable"
        ))
        is_auth = "401" in msg or "403" in msg or "unauthorized" in msg.lower()

        if is_auth:
            self._on_system_message("API 认证失败，请检查 LLM API Key 配置是否正确。")
            self._consecutive_errors = 0
        elif is_network:
            if self._consecutive_errors >= 3:
                self._on_system_message(
                    f"连续 {self._consecutive_errors} 次网络错误，请检查网络连接或 VPN 状态后重试。"
                )
            else:
                self._on_system_message(f"网络请求失败: {safe_msg}")
                self._on_retry_offer(safe_msg)
        else:
            self._on_system_message(f"请求失败: {safe_msg}")

        self._cleanup_worker()

    # ── 重试 ──────────────────────────────────────────────

    def retry(self) -> None:
        """网络错误后重试。"""
        if self._worker and self._worker.is_alive():
            self._worker.cancel()
        self._worker = None
        self._on_system_message("正在重试…")
        self.start_round()

    # ── 内部工具 ──────────────────────────────────────────

    def _cleanup_worker(self) -> None:
        if self._worker:
            self._worker.on_chunk = None
            self._worker.on_finished = None
            self._worker.on_error = None
            self._worker.on_token_usage = None
            self._worker = None

    @staticmethod
    def _sanitize_error_message(msg: str) -> str:
        """M68: 脱敏错误消息，移除文件路径和 API 密钥等敏感信息。"""
        if not msg:
            return msg
        # 移除 Windows 绝对路径 (e.g. D:\path\to\file.py:123)
        msg = re.sub(r'[A-Za-z]:[\\/][^\s,;:"]+', '[path]', msg)
        # 移除 Unix 绝对路径 (e.g. /home/user/project/file.py)
        msg = re.sub(r'/(?:home|Users|usr|tmp|var|etc|opt|root|mnt)/[^\s,;:"]+', '[path]', msg)
        # 移除常见 API 密钥前缀 (OpenAI sk-, Anthropic sk-ant-, 等)
        msg = re.sub(r'\b(sk-[A-Za-z0-9_-]{20,})\b', '[api_key]', msg)
        msg = re.sub(r'\b(sk-ant-[A-Za-z0-9_-]{20,})\b', '[api_key]', msg)
        return msg

    def shutdown(self) -> None:
        """清理所有内部 Qt 资源：worker、信号桥、流式计时器。

        调用方应在持有方（如 ChatWidget）销毁前调用此方法，
        确保无父 QObject 被正确释放。
        """
        if self._worker and self._worker.is_alive():
            self._worker.cancel()
        self._cleanup_worker()
        self._streaming_timer.stop()
        self._streaming_bubble = None
        self._streaming_text = ""
        self._streaming_dirty = False
        if self._cb_bridge is not None:
            try:
                self._cb_bridge.deleteLater()
            except RuntimeError:
                pass
            self._cb_bridge = None
        try:
            self._streaming_timer.deleteLater()
        except RuntimeError:
            pass

    # ── 状态访问 ──────────────────────────────────────────

    @property
    def react_depth(self) -> int:
        return self._react_depth

    @react_depth.setter
    def react_depth(self, value: int) -> None:
        self._react_depth = value

    @property
    def auto_mode(self) -> bool:
        return self._auto_mode

    @auto_mode.setter
    def auto_mode(self, value: bool) -> None:
        self._auto_mode = value

    def cancel_current_round(self) -> None:
        """中断当前流式输出，清理 worker 和流式状态。"""
        try:
            self._streaming_timer.stop()
        except RuntimeError:
            pass
        if self._worker and self._worker.is_alive():
            self._worker.cancel()
        self._worker = None
        if self._streaming_bubble:
            self._on_remove_widget(self._streaming_bubble)
            self._streaming_bubble = None
        self._streaming_text = ""
        self._streaming_dirty = False

    def reset_state(self) -> None:
        """清空对话时重置状态。"""
        self._react_depth = 0
        self._consecutive_errors = 0
        self._streaming_text = ""
        self._streaming_dirty = False
        self._streaming_timer.stop()
        self._streaming_bubble = None
