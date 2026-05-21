"""run_postprocess 工具基础测试 (C2-fix: 补全零执行测试缺口).

测试核心工具的参数校验和错误路径。
不执行真实 LLM 调用。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from tests.conftest import MockAppContext, make_test_collection, make_entry


# ============================================================
# TestRunPostprocessValidation (6 cases)
# ============================================================
class TestRunPostprocessValidation(unittest.TestCase):
    def setUp(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess

        self.func = _tool_run_postprocess

    def test_no_collection_fails(self):
        ctx = MockAppContext()
        r = self.func({}, ctx)
        self.assertFalse(r.success)
        self.assertIn("没有加载翻译集合", r.message)

    def test_invalid_phases_rejected(self):
        ctx = MockAppContext(make_test_collection(5))
        r = self.func({"phases": ["invalid_phase", "nonsense"]}, ctx)
        self.assertFalse(r.success)
        self.assertIn("无效的阶段名", r.message)

    def test_empty_entry_ids_fails(self):
        ctx = MockAppContext(make_test_collection(5))
        r = self.func({"entry_ids": ["nonexistent_key_1", "nonexistent_key_2"]}, ctx)
        self.assertFalse(r.success)
        self.assertIn("均无效", r.message)

    def test_valid_phases_accepted(self):
        """启动后处理应返回 task_id（不等待完成）。"""
        ctx = MockAppContext(make_test_collection(3))
        with patch("src.transbridge.smart_assistant.tools.tool_proofreader.threading.Thread") as mock_thread:
            r = self.func({"phases": ["consistency", "format"]}, ctx)
            self.assertTrue(r.success)
            self.assertIn("task_id", r.data)
            self.assertTrue(mock_thread.called)

    def test_max_workers_clamped(self):
        ctx = MockAppContext(make_test_collection(3))
        with patch("src.transbridge.smart_assistant.tools.tool_proofreader.threading.Thread"):
            r = self.func({"max_workers": 999}, ctx)
            self.assertTrue(r.success)  # 不应崩溃，内部钳位到 8

    def test_default_phases_when_none(self):
        """phases 不传时应默认为全部 6 阶段。"""
        ctx = MockAppContext(make_test_collection(3))
        with patch("src.transbridge.smart_assistant.tools.tool_proofreader.threading.Thread"):
            r = self.func({}, ctx)
            self.assertTrue(r.success)
            self.assertEqual(r.data["phases"], ["consistency", "format", "quality_gate",
                                                  "refinement", "polish", "arbitration"])


# ============================================================
# TestGetQualityReport (3 cases)
# ============================================================
class TestGetQualityReport(unittest.TestCase):
    def setUp(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

        self.func = _tool_get_quality_report
        self.ctx = MockAppContext()

    def test_no_report_returns_empty(self):
        import src.transbridge.smart_assistant.tools.tool_proofreader as mod
        mod._last_report = None
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["reports"], [])

    def test_postprocess_report_message(self):
        import src.transbridge.smart_assistant.tools.tool_proofreader as mod
        mod._last_report = {
            "phase": "postprocess",
            "total_checked": 100,
            "issue_count": 5,
            "auto_fixed": 3,
            "verdict_stats": {"passed": 95, "rejected": 3, "pending": 2},
        }
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertIn("100条", r.message)
        self.assertIn("5个", r.message)

    def test_polish_report_message(self):
        import src.transbridge.smart_assistant.tools.tool_proofreader as mod
        mod._last_report = {
            "phase": "polish",
            "entry_count": 50,
            "polish_level": "moderate",
            "scope": "all",
            "total": 120,
        }
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertIn("润色", r.message)
        self.assertIn("50条", r.message)


# ============================================================
# TestListQualityReports (3 cases)
# ============================================================
class TestListQualityReports(unittest.TestCase):
    def setUp(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import _tool_list_quality_reports

        self.func = _tool_list_quality_reports
        self.ctx = MockAppContext()

    def test_no_esp_path_returns_empty(self):
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertIsNone(r.data["directory"])
        self.assertEqual(r.data["files"], [])

    def test_with_esp_path_nonexistent_dir(self):
        import os
        import tempfile
        self.ctx.esp_path = os.path.join(tempfile.gettempdir(), "nonexistent_mod.esp")
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["files"], [])

    def test_with_limit(self):
        r = self.func({"limit": 5}, self.ctx)
        self.assertTrue(r.success)


# ============================================================
# TestSummarizeHelpers (3 cases)
# ============================================================
class TestSummarizeHelpers(unittest.TestCase):
    def test_summarize_refine_empty(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import _summarize_refine_results

        r = _summarize_refine_results(None)
        self.assertEqual(r, [])

    def test_summarize_polish_empty(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import _summarize_polish_results

        r = _summarize_polish_results(None)
        self.assertEqual(r, [])

    def test_summarize_decisions_empty(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import _summarize_decisions

        r = _summarize_decisions(None)
        self.assertEqual(r, [])


if __name__ == "__main__":
    unittest.main()
