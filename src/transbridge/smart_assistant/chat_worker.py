import logging
import threading
import time

from src.transbridge.smart_assistant.workers.async_worker import AsyncWorker

logger = logging.getLogger(__name__)


class _CancelledByStop(BaseException):
    """穿透 except Exception 的取消信号。"""


class ChatWorker(AsyncWorker):
    """后台线程：调用 LLM 流式 API，通过回调通知。

    Phase 2: 从 QThread+pyqtSignal 迁移到 AsyncWorker(threading.Thread)+回调。
    调用方通过 on_chunk/on_finished/on_error/on_token_usage 属性注册回调，
    需自行保证跨线程 Qt GUI 安全（使用 QTimer.singleShot 桥接）。
    """

    def __init__(self, llm_client, messages: list[dict], max_tokens: int | None = None):
        super().__init__(daemon=True)
        self._client = llm_client
        self._messages = messages
        self._max_tokens = max_tokens

    def run(self) -> None:
        try:
            full_text = ""
            chunk_buffer: list[str] = []
            last_chunk_time = time.monotonic()

            def _chunk_cb(chunk: str) -> None:
                nonlocal full_text, last_chunk_time
                if self._cancelled.is_set():
                    raise _CancelledByStop()
                full_text += chunk
                chunk_buffer.append(chunk)
                now = time.monotonic()
                # Flush every 50ms or every 20 tokens to reduce cross-thread signal pressure
                if len(chunk_buffer) >= 20 or (now - last_chunk_time) >= 0.05:
                    if self.on_chunk:
                        self.on_chunk("".join(chunk_buffer))
                    chunk_buffer.clear()
                    last_chunk_time = now

            self._client.chat_stream(
                self._messages, self._max_tokens, _chunk_cb
            )
            # Flush remaining buffered chunks at end of stream
            if chunk_buffer and self.on_chunk:
                self.on_chunk("".join(chunk_buffer))
                chunk_buffer.clear()
            if not self._cancelled.is_set():
                # MA7: 发射 token 统计（近似估算）
                try:
                    input_chars = sum(len(m.get("content", "")) for m in self._messages)
                    estimated_input = max(1, input_chars // 3)
                    estimated_output = max(1, len(full_text) // 3)
                    model = getattr(self._client, 'model', 'unknown')
                    if self.on_token_usage:
                        self.on_token_usage(model, estimated_input, estimated_output)
                except Exception:
                    logger.debug("Token stats estimation failed", exc_info=True)
                if self.on_finished:
                    self.on_finished(full_text)
        except (_CancelledByStop,):
            pass  # 静默终止
        except Exception as exc:
            if not self._cancelled.is_set() and self.on_error:
                self.on_error(str(exc))

    def cancel(self) -> None:
        super().cancel()
        if self._client:
            try:
                self._client.cancel()
            except Exception:
                logger.debug("Failed to cancel underlying client", exc_info=True)
