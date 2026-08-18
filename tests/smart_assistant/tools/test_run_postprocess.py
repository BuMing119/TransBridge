"""run_postprocess 工具基础测试 (C2-fix: 补全零执行测试缺口).

测试核心工具的参数校验和错误路径。
不执行真实 LLM 调用。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import hashlib
import tempfile
import time

from tests.conftest import MockAppContext, make_test_collection, make_entry


# ============================================================
# TestRunPostprocessValidation (6 cases)
# ============================================================
class TestRunPostprocessValidation(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess

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
        with patch("transbridge.smart_assistant.tools.tool_proofreader.threading.Thread") as mock_thread:
            r = self.func({"phases": ["consistency", "format"]}, ctx)
            self.assertTrue(r.success)
            self.assertIn("task_id", r.data)
            self.assertTrue(mock_thread.called)

    def test_max_workers_clamped(self):
        ctx = MockAppContext(make_test_collection(3))
        with patch("transbridge.smart_assistant.tools.tool_proofreader.threading.Thread"):
            r = self.func({"max_workers": 999}, ctx)
            self.assertTrue(r.success)  # 不应崩溃，内部钳位到 8

    def test_default_phases_when_none(self):
        """phases 不传时应默认为全部 6 阶段。"""
        ctx = MockAppContext(make_test_collection(3))
        with patch("transbridge.smart_assistant.tools.tool_proofreader.threading.Thread"):
            r = self.func({}, ctx)
            self.assertTrue(r.success)
            self.assertEqual(r.data["phases"], ["consistency", "format", "quality_gate",
                                                  "refinement", "polish", "arbitration"])


class TestRunPostprocessProductionPath(unittest.TestCase):
    def tearDown(self):
        from transbridge.smart_assistant.tools.task_manager import TaskManager

        TaskManager.reset()

    def test_candidate_pipeline_commits_once_and_publishes_canonical_report(self):
        from transbridge.application.translation import PostProcessLlmResponse
        from transbridge.smart_assistant.tools.task_manager import TaskManager
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess

        class ControlledPort:
            def apply(self, phase, request):
                values = []
                for candidate in request.candidates:
                    if phase.value == "arbitrate":
                        value = "pass"
                    else:
                        value = f"{phase.value}:{candidate.text}"
                    values.append((candidate.entry_key, value))
                digest = hashlib.sha256(str(values).encode()).hexdigest()
                return PostProcessLlmResponse(tuple(values), digest)

        collection = make_test_collection(2)
        ctx = MockAppContext(collection)
        config = SimpleNamespace(
            api_key="fixture",
            target_lang="zh_CN",
            game_profile="skyrim_se",
            base_url="http://fixture.invalid/v1",
            model="fixture-model",
        )
        processor = SimpleNamespace(_checkers=[])
        with (
            tempfile.TemporaryDirectory() as data_dir,
            patch("transbridge.smart_assistant.tools._common.load_llm_config", return_value=config),
            patch(
                "transbridge.smart_assistant.tools.tool_proofreader.ProofreaderController._build_postprocessor",
                return_value=(processor, None, None, None),
            ),
            patch("transbridge.application.translation.OpenAiPostProcessHttpPort", return_value=ControlledPort()),
            patch("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", return_value=data_dir),
        ):
            result = _tool_run_postprocess(
                {"phases": ["refinement", "polish", "arbitration"]},
                ctx,
            )
            task_id = result.data["task_id"]
            deadline = time.monotonic() + 5
            while TaskManager().get_status(task_id)["status"] not in {"completed", "failed", "cancelled"}:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)

        status = TaskManager().get_status(task_id)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["progress"]["outcome"], "completed")
        self.assertTrue(all(entry.translation.startswith("polish:refine:") for entry in collection))


# ============================================================
# TestGetQualityReport (3 cases)
# ============================================================
class TestGetQualityReport(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

        self.func = _tool_get_quality_report
        self.ctx = MockAppContext()

    def test_no_report_returns_empty(self):
        import transbridge.smart_assistant.tools.tool_proofreader as mod
        mod._last_report = None
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(r.data["reports"], [])

    def test_postprocess_report_message(self):
        import transbridge.smart_assistant.tools.tool_proofreader as mod
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
        import transbridge.smart_assistant.tools.tool_proofreader as mod
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
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_list_quality_reports

        self.func = _tool_list_quality_reports
        self.ctx = MockAppContext()

    def test_no_esp_path_returns_empty(self):
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertNotIn("directory", r.data)
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
    @classmethod
    def setUpClass(cls):
        from transbridge.smart_assistant.tools.tool_proofreader import ProofreaderController
        from transbridge.ui.context import AppContext
        from transbridge.smart_assistant.tools.task_manager import TaskManager
        cls._ctrl = ProofreaderController(AppContext(), TaskManager())

    def test_summarize_refine_empty(self):
        r = self._ctrl._summarize_refine_results(None)
        self.assertEqual(r, [])

    def test_summarize_polish_empty(self):
        r = self._ctrl._summarize_polish_results(None)
        self.assertEqual(r, [])

    def test_summarize_decisions_empty(self):
        r = self._ctrl._summarize_decisions(None)
        self.assertEqual(r, [])


if __name__ == "__main__":
    unittest.main()
