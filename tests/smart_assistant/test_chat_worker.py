"""Story 07: ChatWorker 测试 — 流式响应 / cancel / 错误处理。"""
from __future__ import annotations

import sys
import time
import unittest

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

_app = QApplication.instance()
if _app is None:
    _app = QApplication(sys.argv)


class MockLLMClient:
    """Mock LLM 客户端，可控制流式输出和错误行为。"""

    def __init__(self, chunks=None, should_fail=False, fail_msg="API错误", chunk_delay=0.01):
        self.chunks = chunks or ["你好", "，", "世界"]
        self.should_fail = should_fail
        self.fail_msg = fail_msg
        self.cancelled = False
        self.stream_started = False
        self._chunk_delay = chunk_delay

    def chat_stream(self, messages, max_tokens, callback):
        self.stream_started = True
        if self.should_fail:
            raise RuntimeError(self.fail_msg)
        for chunk in self.chunks:
            if self.cancelled:
                return
            callback(chunk)
            if self._chunk_delay > 0:
                time.sleep(self._chunk_delay)

    def cancel(self):
        self.cancelled = True


class TestChatWorker(unittest.TestCase):
    """ChatWorker 流式响应和错误处理测试。"""

    def setUp(self):
        self._chunks = []
        self._finished = []
        self._errors = []

    def _connect_worker(self, worker):
        worker.on_chunk = lambda c: self._chunks.append(c)
        worker.on_finished = lambda r: self._finished.append(r)
        worker.on_error = lambda e: self._errors.append(e)

    # ── 流式响应 ──────────────────────────────────────────────────

    def test_streaming_chunks(self):
        from transbridge.smart_assistant.chat_worker import ChatWorker
        client = MockLLMClient(chunks=["A", "B", "C"])
        worker = ChatWorker(client, [{"role": "user", "content": "hi"}])
        self._connect_worker(worker)
        worker.start()
        worker.join(timeout=3)
        QApplication.processEvents()
        QApplication.processEvents()  # 确保 Qt 事件循环处理信号
        # 50ms 缓冲可能导致小块合并，验证全部内容被接收即可
        all_chunks = "".join(self._chunks)
        self.assertEqual(all_chunks, "ABC")
        self.assertGreaterEqual(len(self._chunks), 1)
        self.assertEqual(self._finished, ["ABC"])

    def test_streaming_full_text(self):
        from transbridge.smart_assistant.chat_worker import ChatWorker
        client = MockLLMClient(chunks=["Hello", " ", "World", "!"])
        worker = ChatWorker(client, [{"role": "user", "content": "test"}])
        self._connect_worker(worker)
        worker.start()
        worker.join(timeout=3)
        QApplication.processEvents()
        self.assertEqual(len(self._finished), 1)
        self.assertEqual(self._finished[0], "Hello World!")

    # ── 错误处理 ──────────────────────────────────────────────────

    def test_error_signal_on_failure(self):
        from transbridge.smart_assistant.chat_worker import ChatWorker
        client = MockLLMClient(should_fail=True, fail_msg="Connection timeout")
        worker = ChatWorker(client, [{"role": "user", "content": "test"}])
        self._connect_worker(worker)
        worker.start()
        worker.join(timeout=3)
        QApplication.processEvents()
        self.assertGreater(len(self._errors), 0)
        self.assertIn("timeout", self._errors[0].lower())

    def test_no_finished_on_error(self):
        from transbridge.smart_assistant.chat_worker import ChatWorker
        client = MockLLMClient(should_fail=True, fail_msg="Server error")
        worker = ChatWorker(client, [{"role": "user", "content": "test"}])
        self._connect_worker(worker)
        worker.start()
        worker.join(timeout=3)
        QApplication.processEvents()
        self.assertEqual(len(self._finished), 0)

    # ── 取消 ──────────────────────────────────────────────────────

    def test_cancel_stops_streaming(self):
        from transbridge.smart_assistant.chat_worker import ChatWorker
        # 大量 chunk 以便有机会在中间 cancel
        chunks = [f"chunk_{i}" for i in range(100)]
        client = MockLLMClient(chunks=chunks)
        worker = ChatWorker(client, [{"role": "user", "content": "test"}])
        self._connect_worker(worker)
        worker.start()
        time.sleep(0.05)
        worker.cancel()
        worker.join(timeout=3)
        # cancel 后不应有 finished 信号
        self.assertEqual(len(self._finished), 0)

    # ── 空消息 ───────────────────────────────────────────────────

    def test_empty_chunks(self):
        from transbridge.smart_assistant.chat_worker import ChatWorker
        client = MockLLMClient(chunks=[""])  # 至少一个空chunk确保finished触发
        worker = ChatWorker(client, [{"role": "user", "content": "test"}])
        self._connect_worker(worker)
        worker.start()
        worker.join(timeout=3)
        QApplication.processEvents()
        self.assertEqual(len(self._finished), 1)


if __name__ == "__main__":
    unittest.main()
