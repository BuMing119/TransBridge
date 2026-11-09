"""Canonical smart-assistant report publication and history tests."""

from __future__ import annotations

import inspect
from os import utime
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.conftest import make_test_collection
from tests.smart_assistant.postprocess.project_support import project_context
from transbridge.smart_assistant.tools.base import ExecutionContext


class TestReportSystem(unittest.TestCase):
    """The proofreader lists independent run workbooks under the caller's scope."""

    def setUp(self):
        import transbridge.smart_assistant.tools.tool_proofreader as proofreader

        self.ctx = project_context(self, make_test_collection(1))
        proofreader.set_last_report(None, self.ctx)

    def test_legacy_report_projection_helpers_are_removed(self):
        from transbridge.smart_assistant.tools.tool_proofreader import ProofreaderController

        source = inspect.getsource(ProofreaderController)

        self.assertNotIn("ReportGenerator", source)
        self.assertFalse(hasattr(ProofreaderController, "_generate_report"))
        self.assertFalse(hasattr(ProofreaderController, "_summarize_refine_results"))
        self.assertFalse(hasattr(ProofreaderController, "_summarize_polish_results"))
        self.assertFalse(hasattr(ProofreaderController, "_summarize_decisions"))

    def _record_report(self, run_id, *, owner="ui", session=None):
        task_store = self.ctx.task_store
        task_store.recovery().create({
            "task_id": run_id,
            "owner_id": owner,
            "session_id": session,
            "project_id": "removed-project",
            "variant_id": "removed-version",
            "origin": "smart-assistant",
            "mode": "postprocess",
            "config_digest": "fixture",
            "state": "completed",
            "created_at": "2026-11-09T00:00:00+00:00",
            "sources": [],
            "result_kind": "postprocess",
            "results_only": True,
        })
        reports = task_store.run_directory(run_id) / "reports"
        reports.mkdir(parents=True)
        return reports

    def test_reports_are_scoped_and_available_without_an_active_project(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_list_quality_reports

        (self._record_report("mine") / "report.xlsx").write_bytes(b"mine")
        (self._record_report("foreign", owner="other") / "foreign.xlsx").write_bytes(b"foreign")
        (self._record_report("session", session="other") / "session.xlsx").write_bytes(b"session")
        app = SimpleNamespace(
            active_version_identity=None, task_store=self.ctx.task_store, runtime_context=self.ctx.runtime_context
        )
        result = _tool_list_quality_reports({}, ExecutionContext(app_context=app))
        self.assertTrue(result.success, result.message)
        self.assertEqual(
            [(item["result_id"], item["name"]) for item in result.data["files"]], [("mine", "report.xlsx")]
        )

    def test_lists_only_run_workbooks_in_modified_order_and_ignores_old_report_tree(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_list_quality_reports

        with tempfile.TemporaryDirectory() as data_dir:
            legacy = Path(data_dir) / "reports" / "postprocess" / "legacy.xlsx"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"legacy")
            reports = self._record_report("run")
            first, second = reports / "first.xlsx", reports / "second.xlsx"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            (reports / "metadata.json").write_text("{}", encoding="utf-8")
            utime(first, (1_700_000_000, 1_700_000_000))
            utime(second, (1_700_000_100, 1_700_000_100))

            with patch("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", return_value=data_dir):
                result = _tool_list_quality_reports({"limit": 10}, self.ctx)
                limited = _tool_list_quality_reports({"limit": 1}, self.ctx)

        self.assertTrue(result.success, result.message)
        self.assertEqual([item["name"] for item in result.data["files"]], ["second.xlsx", "first.xlsx"])
        self.assertTrue(limited.success, limited.message)
        self.assertEqual([item["name"] for item in limited.data["files"]], ["second.xlsx"])


class TestLastReportIntegrity(unittest.TestCase):
    """The in-memory summary preserves bundle artifacts and structured diagnostics."""

    def setUp(self):
        import transbridge.smart_assistant.tools.tool_proofreader as proofreader

        self.proofreader = proofreader
        self.ctx = project_context(self, make_test_collection(1))
        proofreader.set_last_report(None, self.ctx)

    def tearDown(self):
        self.proofreader.set_last_report(None, self.ctx)

    def test_get_quality_report_without_report(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

        result = _tool_get_quality_report({}, self.ctx)

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
        self.proofreader.set_last_report(report, self.ctx)

        result = _tool_get_quality_report({}, self.ctx)

        self.assertTrue(result.success)
        stored = result.data["reports"][0]
        self.assertEqual(stored["report_file"], report["report_file"])
        self.assertEqual(stored["report_files"], report["report_files"])
        self.assertEqual(stored["report_diagnostics"], report["report_diagnostics"])
        self.assertIn("报告文件", result.message)


if __name__ == "__main__":
    unittest.main()
