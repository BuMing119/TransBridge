import threading

from PyQt6.QtCore import QThread, pyqtSignal


class _CancelledByStop(BaseException):
    """穿透 except Exception 的取消信号。"""


class ChatWorker(QThread):
    """后台线程：调用 LLM 流式 API，通过信号回调。"""

    chunk = pyqtSignal(str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, llm_client, messages: list[dict], max_tokens: int = 2048):
        super().__init__()
        self._client = llm_client
        self._messages = messages
        self._max_tokens = max_tokens
        self._cancelled = threading.Event()

    def run(self) -> None:
        try:
            full_text = ""

            def _chunk_cb(chunk: str) -> None:
                nonlocal full_text
                if self._cancelled.is_set():
                    raise _CancelledByStop()
                full_text += chunk
                self.chunk.emit(chunk)

            self._client.chat_stream(
                self._messages, self._max_tokens, _chunk_cb
            )
            if not self._cancelled.is_set():
                self.finished.emit(full_text)
        except (_CancelledByStop,):
            pass  # 静默终止
        except Exception as exc:
            if not self._cancelled.is_set():
                self.error.emit(str(exc))

    def cancel(self) -> None:
        self._cancelled.set()
        if self._client:
            try:
                self._client.cancel()
            except Exception:
                pass
