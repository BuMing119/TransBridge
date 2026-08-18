"""Story 07: ObservabilityCollector 测试 — token 统计 / 追踪持久化 / 过期清理。"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance()
if _app is None:
    _app = QApplication(sys.argv)


class TestObservabilityCollector(unittest.TestCase):
    """可观测性收集器核心逻辑测试。"""

    def setUp(self):
        from transbridge.smart_assistant.observability.collector import ObservabilityCollector
        self._tmp = tempfile.mkdtemp()
        self.collector = ObservabilityCollector(storage_dir=Path(self._tmp))

    def tearDown(self):
        # 等待 daemon 线程完成异步文件写入（_save_trace 在后台线程中运行）
        time.sleep(0.3)
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── 会话生命周期 ────────────────────────────────────────────

    def test_start_conversation_creates_trace(self):
        self.collector.start_conversation("conv_001")
        self.assertIsNotNone(self.collector._active)
        self.assertEqual(self.collector._active.conv_id, "conv_001")

    def test_start_new_conversation_ends_previous(self):
        self.collector.start_conversation("conv_001")
        trace = self.collector.end_conversation()
        self.assertIsNotNone(trace)
        self.assertIsNone(self.collector._active)

    def test_end_conversation_without_active(self):
        trace = self.collector.end_conversation()
        self.assertIsNone(trace)

    # ── Token 统计 ──────────────────────────────────────────────

    def test_token_stats_accumulate(self):
        self.collector.start_conversation("conv_001")
        self.collector.on_llm_tokens("gpt-4", 100, 50)
        self.collector.on_llm_tokens("gpt-4", 200, 100)
        self.assertEqual(self.collector._session_tokens.input_tokens, 300)
        self.assertEqual(self.collector._session_tokens.output_tokens, 150)

    def test_session_tokens_reset_on_new_conversation(self):
        """M12: 新会话重置 session token。"""
        self.collector.start_conversation("conv_001")
        self.collector.on_llm_tokens("gpt-4", 500, 200)
        self.collector.end_conversation()
        self.collector.start_conversation("conv_002")
        # M12: session_tokens 应在 start_conversation 时重置为 0
        self.assertEqual(self.collector._session_tokens.input_tokens, 0)

    # ── 追踪持久化 ──────────────────────────────────────────────

    def test_save_trace_persists_file(self):
        self.collector.start_conversation("conv_save_test")
        self.collector.on_step_started(1, "test_tool")
        from transbridge.smart_assistant.execution_engine import StepResult
        self.collector.on_step_finished(StepResult(
            step_id=1, tool="test_tool", success=True,
            message="OK", duration_ms=42,
        ))
        self.collector.end_conversation()
        # 等待 daemon 线程完成异步文件写入
        time.sleep(0.5)
        expected_path = Path(self._tmp) / "conv_save_test.json"
        self.assertTrue(expected_path.exists(), f"Expected {expected_path} to exist")

    def test_cleanup_old_traces(self):
        import time
        self.collector.start_conversation("conv_old")
        self.collector.end_conversation()
        # 等待 daemon 线程完成异步文件写入
        time.sleep(0.5)
        old_path = Path(self._tmp) / "conv_old.json"
        self.assertTrue(old_path.exists())
        # 修改 mtime 为 31 天前
        old_time = time.time() - 31 * 86400
        os.utime(str(old_path), (old_time, old_time))
        self.collector._cleanup_old(max_age_days=30)
        self.assertFalse(old_path.exists(), "31 天前的追踪应被清理")

    # ── 工具调用记录 ────────────────────────────────────────────

    def test_tools_called_recorded(self):
        self.collector.start_conversation("conv_tools")
        self.collector.on_step_started(1, "translate")
        from transbridge.smart_assistant.execution_engine import StepResult
        self.collector.on_step_finished(StepResult(
            step_id=1, tool="translate", success=True,
            message="翻译完成", duration_ms=500,
        ))
        self.assertEqual(len(self.collector._active.tools_called), 1)
        self.assertEqual(self.collector._active.tools_called[0].tool_name, "translate")

    def test_retry_tracking(self):
        self.collector.start_conversation("conv_retry")
        self.collector.on_step_started(1, "flakey_tool")
        from transbridge.smart_assistant.execution_engine import StepResult
        self.collector.on_step_finished(StepResult(
            step_id=1, tool="flakey_tool", success=False,
            message="timeout", duration_ms=100,
        ))
        self.collector.on_step_retrying(1, 1)
        self.collector.on_step_retrying(1, 2)
        self.assertEqual(self.collector._active.tools_called[-1].retry_count, 2)


if __name__ == "__main__":
    unittest.main()
