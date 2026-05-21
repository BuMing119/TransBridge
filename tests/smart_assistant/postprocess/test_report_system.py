"""Story 25: 报告系统测试。

Test D: _generate_report / _summarize_* / list_quality_reports
Test E: _last_report 数据完整性（set/get 回环 + 无报告行为）
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.conftest import make_entry, MockAppContext

from src.transbridge.ai_translator.post_processor.base import PostProcessResult
from src.transbridge.smart_assistant.tools.base import ExecutionContext


class TestReportSystem(unittest.TestCase):
    """验收标准: 报告生成、摘要函数、文件列表功能正确。"""

    @classmethod
    def setUpClass(cls):
        import src.transbridge.smart_assistant.tools.tool_proofreader as tpr
        cls.tpr = tpr

    def setUp(self):
        self.tpr._last_report = None

    # ── _summarize_refine_results ──

    def test_d1_summarize_refine_empty(self):
        result = self.tpr._summarize_refine_results(None)
        self.assertEqual(result, [])

        result = self.tpr._summarize_refine_results({})
        self.assertEqual(result, [])

    def test_d2_summarize_refine_normal(self):
        mock_ref = SimpleNamespace(
            refined_translation="修复后的译文",
            confidence=0.85,
        )
        refine_results = {"entry_001": mock_ref}

        result = self.tpr._summarize_refine_results(refine_results)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_id"], "entry_001")
        self.assertEqual(result[0]["refined_translation"], "修复后的译文")
        self.assertEqual(result[0]["confidence"], 0.85)

    def test_d3_summarize_refine_missing_attrs(self):
        mock_ref = SimpleNamespace()
        refine_results = {"entry_001": mock_ref}

        result = self.tpr._summarize_refine_results(refine_results)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["refined_translation"], "")
        self.assertEqual(result[0]["confidence"], 0.0)

    # ── _summarize_polish_results ──

    def test_d4_summarize_polish_empty(self):
        self.assertEqual(self.tpr._summarize_polish_results(None), [])
        self.assertEqual(self.tpr._summarize_polish_results({}), [])

    def test_d5_summarize_polish_normal(self):
        mock_pol = SimpleNamespace(
            polished_translation="润色后的译文",
            confidence=0.92,
            changes=[
                {"aspect": "fluency", "before": "old", "after": "new"},
                {"aspect": "tone", "before": "formal", "after": "casual"},
            ],
        )
        polish_results = {"entry_001": mock_pol}

        result = self.tpr._summarize_polish_results(polish_results)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_id"], "entry_001")
        self.assertEqual(result[0]["polished_translation"], "润色后的译文")
        self.assertEqual(result[0]["confidence"], 0.92)
        self.assertEqual(result[0]["changes_count"], 2)

    def test_d6_summarize_polish_empty_changes(self):
        mock_pol = SimpleNamespace(
            polished_translation="译文",
            confidence=0.5,
            changes=[],
        )
        result = self.tpr._summarize_polish_results({"e1": mock_pol})
        self.assertEqual(result[0]["changes_count"], 0)

    def test_d7_summarize_polish_none_changes(self):
        mock_pol = SimpleNamespace(
            polished_translation="译文",
            confidence=0.5,
            changes=None,
        )
        result = self.tpr._summarize_polish_results({"e1": mock_pol})
        self.assertEqual(result[0]["changes_count"], 0)

    # ── _summarize_decisions ──

    def test_d8_summarize_decisions_empty(self):
        self.assertEqual(self.tpr._summarize_decisions(None), [])
        self.assertEqual(self.tpr._summarize_decisions({}), [])

    def test_d9_summarize_decisions_normal(self):
        mock_dec = SimpleNamespace(
            verdict="pass",
            reason="质量合格",
            suggested_action="接受译文",
            confidence=0.95,
        )
        decisions = {"entry_001": mock_dec}

        result = self.tpr._summarize_decisions(decisions)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["entry_id"], "entry_001")
        self.assertEqual(result[0]["verdict"], "pass")
        self.assertEqual(result[0]["reason"], "质量合格")
        self.assertEqual(result[0]["suggested_action"], "接受译文")
        self.assertEqual(result[0]["confidence"], 0.95)

    def test_d10_summarize_decisions_missing_attrs(self):
        mock_dec = SimpleNamespace()
        result = self.tpr._summarize_decisions({"e1": mock_dec})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["verdict"], "")
        self.assertEqual(result[0]["reason"], "")
        self.assertEqual(result[0]["suggested_action"], "")
        self.assertEqual(result[0]["confidence"], 0.0)

    # ── _generate_report ──

    def test_d11_generate_report_esp_path_none_returns_none(self):
        result = self.tpr._generate_report([], None, None, None)
        self.assertIsNone(result)

    def test_d12_generate_report_pp_result_none_returns_none(self):
        result = self.tpr._generate_report([], None, "/fake/path.esp", MagicMock())
        self.assertIsNone(result)

    def test_d13_generate_report_simplenamespace_fields(self):
        from pathlib import Path

        pp_result = PostProcessResult(total_checked=5, issue_count=2)
        pp_result.refine_results = {}
        pp_result.polish_results = {}
        pp_result.decisions = {}

        entries = [make_entry("e0"), make_entry("e1")]
        esp_path = "/tmp/test.esp"
        esp_stem = Path(esp_path).stem

        fake_result = SimpleNamespace(
            success_count=len(entries),
            failed_count=0,
            skipped_count=0,
            new_dynamic_terms=0,
            post_process_result=pp_result,
        )

        self.assertEqual(fake_result.success_count, 2)
        self.assertEqual(fake_result.failed_count, 0)
        self.assertEqual(fake_result.skipped_count, 0)
        self.assertEqual(fake_result.new_dynamic_terms, 0)
        self.assertIs(fake_result.post_process_result, pp_result)

    # ── list_quality_reports ──

    def test_d14_list_quality_reports_no_esp_path(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import (
            _tool_list_quality_reports,
        )
        ctx = MockAppContext(collection=None)
        ctx.esp_path = None
        ec = ExecutionContext(app_context=ctx)

        result = _tool_list_quality_reports({}, ec)
        self.assertTrue(result.success)
        self.assertIn("未加载 ESP", result.message)
        self.assertEqual(result.data["files"], [])
        self.assertIsNone(result.data["directory"])

    def test_d15_list_quality_reports_nonexistent_directory(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import (
            _tool_list_quality_reports,
        )
        ctx = MockAppContext(collection=None)
        ctx.esp_path = os.path.join(tempfile.gettempdir(), "nonexistent_plugin.esp")
        ec = ExecutionContext(app_context=ctx)

        result = _tool_list_quality_reports({}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["files"], [])

    def test_d16_list_quality_reports_with_limit(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import (
            _tool_list_quality_reports,
        )
        ctx = MockAppContext(collection=None)
        ctx.esp_path = os.path.join(tempfile.gettempdir(), "test_limit.esp")
        ec = ExecutionContext(app_context=ctx)

        result = _tool_list_quality_reports({"limit": 10}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["files"], [])


class TestLastReportIntegrity(unittest.TestCase):
    """验收标准: _last_report get/set 回环完整，无报告时行为正确。"""

    @classmethod
    def setUpClass(cls):
        import src.transbridge.smart_assistant.tools.tool_proofreader as tpr
        cls.tpr = tpr

    def setUp(self):
        self.tpr._last_report = None

    def tearDown(self):
        self.tpr._last_report = None

    def test_e1_get_quality_report_no_report(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import (
            _tool_get_quality_report,
        )
        ctx = MockAppContext()
        ec = ExecutionContext(app_context=ctx)

        result = _tool_get_quality_report({}, ec)
        self.assertTrue(result.success)
        self.assertIn("暂无质量报告", result.message)
        self.assertEqual(result.data["reports"], [])

    def test_e2_last_report_structure_postprocess(self):
        self.tpr._last_report = {
            "phase": "postprocess",
            "phases": ["consistency", "format", "quality_gate",
                      "refinement", "polish", "arbitration"],
            "total_checked": 50,
            "issue_count": 5,
            "auto_fixed": 3,
            "needs_review": ["entry_005", "entry_012"],
            "verdict_stats": {"passed": 45, "rejected": 2, "pending": 3},
            "issues": [
                {
                    "entry_id": "entry_001",
                    "issue_type": "term_mismatch",
                    "severity": "warning",
                    "message": "术语不一致",
                    "original": "Hello",
                    "translation": "你好",
                    "suggestion": "建议使用: 您好",
                },
            ],
            "refine_results": [
                {"entry_id": "entry_001", "refined_translation": "您好", "confidence": 0.9},
            ],
            "polish_results": [],
            "decisions": [
                {"entry_id": "entry_001", "verdict": "pass",
                 "reason": "修复后合格", "suggested_action": "接受", "confidence": 0.9},
            ],
            "report_file": "/fake/path/report.xlsx",
            "timestamp": time.time(),
        }

        from src.transbridge.smart_assistant.tools.tool_proofreader import (
            _tool_get_quality_report,
        )
        ctx = MockAppContext()
        ec = ExecutionContext(app_context=ctx)

        result = _tool_get_quality_report({}, ec)
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["reports"]), 1)
        report = result.data["reports"][0]

        self.assertEqual(report["phase"], "postprocess")
        self.assertEqual(report["total_checked"], 50)
        self.assertEqual(report["issue_count"], 5)
        self.assertEqual(report["auto_fixed"], 3)
        self.assertEqual(report["needs_review"], ["entry_005", "entry_012"])
        self.assertEqual(
            report["verdict_stats"],
            {"passed": 45, "rejected": 2, "pending": 3},
        )
        self.assertEqual(len(report["issues"]), 1)
        self.assertEqual(len(report["refine_results"]), 1)
        self.assertEqual(len(report["decisions"]), 1)
        self.assertEqual(report["report_file"], "/fake/path/report.xlsx")
        self.assertIn("timestamp", report)

    def test_e3_get_quality_report_message_format(self):
        self.tpr._last_report = {
            "phase": "postprocess",
            "phases": ["consistency"],
            "total_checked": 10,
            "issue_count": 2,
            "auto_fixed": 1,
            "needs_review": [],
            "verdict_stats": {"passed": 8, "rejected": 1, "pending": 1},
            "issues": [],
            "refine_results": [],
            "polish_results": [],
            "decisions": [],
            "report_file": None,
            "timestamp": time.time(),
        }

        from src.transbridge.smart_assistant.tools.tool_proofreader import (
            _tool_get_quality_report,
        )
        ctx = MockAppContext()
        ec = ExecutionContext(app_context=ctx)

        result = _tool_get_quality_report({}, ec)
        self.assertTrue(result.success)
        self.assertIn("检查10条", result.message)
        self.assertIn("发现问题2个", result.message)
        self.assertIn("自动修复1个", result.message)
        self.assertIn("通过8", result.message)
        self.assertIn("打回1", result.message)
        self.assertIn("待审1", result.message)

    def test_e4_last_report_polish_structure(self):
        polish_report = {
            "phase": "polish",
            "entry_count": 20,
            "polish_level": "moderate",
            "scope": "all",
            "total": 20,
            "timestamp": time.time(),
        }

        self.assertEqual(polish_report["phase"], "polish")
        self.assertEqual(polish_report["entry_count"], 20)
        self.assertEqual(polish_report["polish_level"], "moderate")
        self.assertEqual(polish_report["scope"], "all")
        self.assertEqual(polish_report["total"], 20)
        self.assertIn("timestamp", polish_report)

        self.assertNotIn("total_checked", polish_report)
        self.assertNotIn("issue_count", polish_report)
        self.assertNotIn("auto_fixed", polish_report)

    def test_e5_report_missing_optional_fields(self):
        self.tpr._last_report = {
            "phase": "postprocess",
            "phases": ["consistency"],
            "total_checked": 5,
            "issue_count": 0,
            "auto_fixed": 0,
            "needs_review": [],
            "issues": [],
            "refine_results": [],
            "polish_results": [],
            "decisions": [],
            "report_file": None,
            "timestamp": time.time(),
        }

        from src.transbridge.smart_assistant.tools.tool_proofreader import (
            _tool_get_quality_report,
        )
        ctx = MockAppContext()
        ec = ExecutionContext(app_context=ctx)

        result = _tool_get_quality_report({}, ec)
        self.assertTrue(result.success)
        self.assertNotIn("裁决", result.message)
