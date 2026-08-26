"""Canonical smart-assistant report publication and history tests."""

from __future__ import annotations

import inspect
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.conftest import MockAppContext
from transbridge.smart_assistant.tools.base import ExecutionContext


class TestReportSystem(unittest.TestCase):
    """The proofreader consumes canonical snapshots and keeps old workbooks discoverable."""

    def setUp(self):
        import transbridge.smart_assistant.tools.tool_proofreader as proofreader

        proofreader.set_last_report(None)

    def test_legacy_report_projection_helpers_are_removed(self):
        from transbridge.smart_assistant.tools.tool_proofreader import ProofreaderController

        source = inspect.getsource(ProofreaderController)

        self.assertNotIn("ReportGenerator", source)
        self.assertFalse(hasattr(ProofreaderController, "_generate_report"))
        self.assertFalse(hasattr(ProofreaderController, "_summarize_refine_results"))
        self.assertFalse(hasattr(ProofreaderController, "_summarize_polish_results"))
        self.assertFalse(hasattr(ProofreaderController, "_summarize_decisions"))

    def test_list_quality_reports_without_esp_is_empty(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_list_quality_reports

        ctx = MockAppContext(collection=None)
        ctx.esp_path = None

        result = _tool_list_quality_reports({}, ExecutionContext(app_context=ctx))

        self.assertTrue(result.success)
        self.assertIn("未加载 ESP", result.message)
        self.assertEqual(result.data["files"], [])

    def test_report_directory_without_esp_falls_back_under_data_dir(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _resolve_report_directory

        with (
            tempfile.TemporaryDirectory() as data_dir,
            patch("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", return_value=data_dir),
        ):
            report_dir = _resolve_report_directory(MockAppContext())

        self.assertEqual(report_dir, Path(data_dir) / "reports" / "postprocess")

    def test_list_quality_reports_includes_legacy_and_canonical_xlsx(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_list_quality_reports

        ctx = MockAppContext(collection=None)
        ctx.esp_path = "C:/games/Data/Demo.esp"
        with tempfile.TemporaryDirectory() as data_dir:
            ai_dir = Path(data_dir) / "ai_translator" / "Demo"
            reports_dir = ai_dir / "reports"
            reports_dir.mkdir(parents=True)
            legacy = reports_dir / "translate_report_Demo_20250825_120000.xlsx"
            canonical = reports_dir / "postprocess-report-0123456789abcdef.xlsx"
            ignored = reports_dir / "postprocess-report-0123456789abcdef.json"
            legacy.write_bytes(b"legacy")
            canonical.write_bytes(b"canonical")
            ignored.write_text("{}", encoding="utf-8")
            legacy.touch()
            time.sleep(0.01)
            canonical.touch()

            with patch(
                "transbridge.paratranz.config_manager.LLMConfig.get_ai_translator_dir",
                return_value=str(ai_dir),
            ):
                result = _tool_list_quality_reports(
                    {"limit": 10},
                    ExecutionContext(app_context=ctx),
                )

        self.assertTrue(result.success)
        self.assertEqual(
            [item["name"] for item in result.data["files"]],
            [canonical.name, legacy.name],
        )


class TestLastReportIntegrity(unittest.TestCase):
    """The in-memory summary preserves bundle artifacts and structured diagnostics."""

    def setUp(self):
        import transbridge.smart_assistant.tools.tool_proofreader as proofreader

        self.proofreader = proofreader
        proofreader.set_last_report(None)

    def tearDown(self):
        self.proofreader.set_last_report(None)

    def test_get_quality_report_without_report(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

        result = _tool_get_quality_report({}, MockAppContext())

        self.assertTrue(result.success)
        self.assertIn("暂无质量报告", result.message)
        self.assertEqual(result.data["reports"], [])

    def test_get_quality_report_preserves_canonical_bundle_metadata(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

        report = {
            "phase": "postprocess",
            "phases": ["consistency"],
            "total_checked": 10,
            "issue_count": 2,
            "auto_fixed": 1,
            "needs_review": ["entry-2"],
            "verdict_stats": {"passed": 8, "rejected": 2, "pending": 0},
            "issues": [],
            "report_file": "/reports/postprocess-report-id.xlsx",
            "report_files": [
                "/reports/postprocess-report-id.json",
                "/reports/postprocess-report-id.csv",
                "/reports/postprocess-report-id.xlsx",
            ],
            "report_diagnostics": [
                {
                    "code": "REPORT_RENDER_FAILED",
                    "message": "CSV renderer failed",
                    "severity": "error",
                    "category": "dependency",
                    "retryable": False,
                    "details": {"renderer": "csv"},
                }
            ],
            "timestamp": time.time(),
        }
        self.proofreader.set_last_report(report)

        result = _tool_get_quality_report({}, MockAppContext())

        self.assertTrue(result.success)
        stored = result.data["reports"][0]
        self.assertEqual(stored["report_file"], report["report_file"])
        self.assertEqual(stored["report_files"], report["report_files"])
        self.assertEqual(stored["report_diagnostics"], report["report_diagnostics"])
        self.assertIn("报告文件", result.message)


if __name__ == "__main__":
    unittest.main()
