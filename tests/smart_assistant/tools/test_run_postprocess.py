"""run_postprocess 工具基础测试 (C2-fix: 补全零执行测试缺口).

测试核心工具的参数校验和错误路径。
不执行真实 LLM 调用。
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.conftest import MockAppContext, make_entry, make_test_collection
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


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
        """无参数调用默认运行 Proofread 校对。"""
        ctx = MockAppContext(make_test_collection(3))
        with patch("transbridge.smart_assistant.tools.tool_proofreader.threading.Thread"):
            r = self.func({}, ctx)
            self.assertTrue(r.success)
            self.assertEqual(r.data["phases"], ["proofread"])
            self.assertEqual(r.data["strategy"], "proofread")
            self.assertEqual(r.data["profile"], "builtin:polish")

    def test_proofread_rejects_strict_phases_and_accepts_legacy_alias(self):
        ctx = MockAppContext(make_test_collection(5))

        result = self.func({"strategy": "combined", "phases": ["polish"]}, ctx)

        self.assertFalse(result.success)
        self.assertIn("proofread", result.message)

    def test_runtime_limit_overrides_are_frozen_in_task_metadata(self):
        ctx = MockAppContext(make_test_collection(5))
        with patch("transbridge.smart_assistant.tools.tool_proofreader.threading.Thread"):
            result = self.func(
                {
                    "scope": "all",
                    "max_concurrent": 7,
                    "max_tokens_per_batch": 3200,
                    "max_output_tokens": 900,
                    "max_terms_per_batch": 80,
                },
                ctx,
            )

        self.assertTrue(result.success)
        self.assertEqual(
            result.data["limits"],
            {
                "max_concurrent": 7,
                "max_tokens_per_batch": 3200,
                "max_output_tokens": 900,
                "max_terms_per_batch": 80,
            },
        )

    def test_named_custom_profile_supplies_strategy_stages_intensity_and_limits(self):
        from transbridge.application.translation.custom_workflow_profile import (
            CustomWorkflowProfile,
            CustomWorkflowProfileDocument,
        )
        from transbridge.config.llm import LLMConfig

        profile = CustomWorkflowProfile.create(
            "成本优先",
            base_mode="polish",
            strategy="strict",
            workflow={
                "enable_post_process": True,
                "pp_enable_consistency_check": True,
                "pp_enable_format_validation": False,
                "pp_enable_quality_gate": False,
                "pp_enable_refinement": False,
                "pp_enable_polish": True,
                "pp_polish_level": "light",
                "pp_enable_arbitration": False,
            },
            limits={
                "max_concurrent": 4,
                "max_tokens_per_batch": 2400,
                "max_output_tokens": 600,
                "max_terms_per_batch": 30,
            },
        )
        repository = SimpleNamespace(
            load=lambda: CustomWorkflowProfileDocument(profile.id, (profile,)),
        )
        ctx = MockAppContext(make_test_collection(5))
        with (
            patch(
                "transbridge.config.ai_workflow_profiles.AiWorkflowProfileRepository",
                return_value=repository,
            ),
            patch(
                "transbridge.smart_assistant.tools._common.load_llm_config",
                return_value=LLMConfig(),
            ),
            patch("transbridge.smart_assistant.tools.tool_proofreader.threading.Thread"),
        ):
            result = self.func({"profile": "成本优先", "scope": "all"}, ctx)

        self.assertTrue(result.success)
        self.assertEqual(result.data["profile"], "custom:成本优先")
        self.assertEqual(result.data["profile_id"], profile.id)
        self.assertEqual(result.data["strategy"], "strict")
        self.assertEqual(result.data["stages"], ["consistency", "polish"])
        self.assertEqual(result.data["intensity"], "light")
        self.assertEqual(result.data["limits"]["max_concurrent"], 4)

    def test_tool_schema_exposes_preset_and_budget_parameters(self):
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        spec = ToolRegistry.get("run_postprocess")

        self.assertIsNotNone(spec)
        self.assertTrue(
            {
                "profile",
                "strategy",
                "phases",
                "entry_ids",
                "scope",
                "intensity",
                "max_concurrent",
                "max_tokens_per_batch",
                "max_output_tokens",
                "max_terms_per_batch",
            }.issubset(spec.parameters["properties"])
        )


class TestRunPostprocessProductionPath(unittest.TestCase):
    def tearDown(self):
        from transbridge.smart_assistant.tools.task_manager import TaskManager

        TaskManager.reset()

    def test_auto_fixed_counts_only_accepted_committed_candidates(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _count_committed_fixes

        candidates = (
            SimpleNamespace(accepted=True, before_text="old", text="new"),
            SimpleNamespace(accepted=False, before_text="old", text="rejected-new"),
            SimpleNamespace(accepted=True, before_text="same", text="same"),
        )

        self.assertEqual(_count_committed_fixes(candidates), 1)

    def test_default_proofread_batches_entries_and_reports_effective_profile(self):
        from transbridge.config.llm import LLMConfig
        from transbridge.smart_assistant.tools.task_manager import TaskManager
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess, get_last_report

        class ProofreadClient:
            def __init__(self):
                self.calls = 0
                self.max_tokens = []

            def chat(self, messages, max_tokens=0):
                self.calls += 1
                self.max_tokens.append(max_tokens)
                payload = json.loads(messages[-1]["content"])
                return json.dumps({
                    "results": [
                        {
                            "entry_key": entry["entry_key"],
                            "final_translation": f"fixed:{entry['current_translation']}",
                        }
                        for entry in payload["entries"]
                    ]
                })

        client = ProofreadClient()
        runtime = SimpleNamespace(
            client=client,
            log_store=SimpleNamespace(log_dir="proofread-log"),
            close=lambda: None,
        )
        term_manager = SimpleNamespace(load_all=lambda: None, match_terms=lambda _texts: {})
        collection = TranslationEntryCollection([
            make_entry("entry_001", original="One", translation="Old one", stage=1),
            make_entry("entry_002", original="Two", translation="Old two", stage=1),
        ])
        ctx = MockAppContext(collection)
        config = LLMConfig(api_key="fixture", model="fixture-model", max_tokens_per_batch=10_000)
        with (
            tempfile.TemporaryDirectory() as data_dir,
            patch("transbridge.smart_assistant.tools._common.load_llm_config", return_value=config),
            patch(
                "transbridge.smart_assistant.tools._postprocess_tool_runtime.create_workflow_llm_runtime",
                return_value=runtime,
            ),
            patch(
                "transbridge.ai_translator.term_database.TermDatabaseManager",
                return_value=term_manager,
            ),
            patch("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", return_value=data_dir),
            patch(
                "transbridge.paratranz.config_manager.LLMConfig.get_ai_translator_dir",
                return_value=str(Path(data_dir) / "ai_translator" / "Demo"),
            ),
        ):
            ctx.esp_path = str(Path(data_dir) / "Demo.esp")
            result = _tool_run_postprocess({}, ctx)
            self.assertTrue(result.success, result.message)
            task_id = result.data["task_id"]
            deadline = time.monotonic() + 5
            while TaskManager().get_status(task_id)["status"] not in {"completed", "failed", "cancelled"}:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            report = get_last_report()

        self.assertEqual(TaskManager().get_status(task_id)["status"], "completed")
        self.assertEqual(client.calls, 1)
        self.assertEqual(client.max_tokens, [0])
        self.assertTrue(all(entry.translation.startswith("fixed:") for entry in collection))
        self.assertEqual(report["profile"], "builtin:polish")
        self.assertEqual(report["strategy"], "proofread")
        self.assertEqual(report["stages"], ["proofread"])
        self.assertEqual(report["scope"], "all")
        self.assertEqual(report["log_dir"], "proofread-log")

    def test_candidate_pipeline_commits_once_and_publishes_canonical_report(self):
        from transbridge.config.llm import LLMConfig
        from transbridge.smart_assistant.tools.task_manager import TaskManager
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess

        class ControlledClient:
            def chat(self, messages, max_tokens=0):
                payload = json.loads(messages[-1]["content"])
                phase = payload["phase"]
                results = []
                for entry in payload["entries"]:
                    value = "pass" if phase == "arbitrate" else f"{phase}:{entry['current']}"
                    results.append({"entry_key": entry["entry_key"], "value": value})
                return json.dumps({"results": results})

        collection = TranslationEntryCollection([
            make_entry("entry_001", original="One", translation="Old one", stage=1),
            make_entry("entry_002", original="Two", translation="Old two", stage=1),
        ])
        ctx = MockAppContext(collection)
        config = LLMConfig(
            api_key="fixture",
            model="fixture-model",
            max_concurrent=2,
            max_output_tokens=100,
        )
        runtime = SimpleNamespace(
            client=ControlledClient(),
            log_store=SimpleNamespace(log_dir="fixture-log"),
            close=lambda: None,
        )
        prompts_dir = Path(__file__).resolve().parents[3] / "data" / "prompts"
        with (
            tempfile.TemporaryDirectory() as data_dir,
            patch("transbridge.smart_assistant.tools._common.load_llm_config", return_value=config),
            patch(
                "transbridge.smart_assistant.tools._postprocess_tool_runtime.create_workflow_llm_runtime",
                return_value=runtime,
            ),
            patch("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", return_value=data_dir),
            patch(
                "transbridge.paratranz.config_manager.LLMConfig.get_ai_translator_dir",
                return_value=str(Path(data_dir) / "ai_translator" / "Demo"),
            ),
            patch(
                "transbridge.ai_translator.post_processor.quality_gate._get_prompts_dir",
                return_value=prompts_dir,
            ),
            patch(
                "transbridge.ai_translator.post_processor.llm_refiner._get_prompts_dir",
                return_value=prompts_dir,
            ),
            patch(
                "transbridge.ai_translator.post_processor.polisher._get_prompts_dir",
                return_value=prompts_dir,
            ),
            patch(
                "transbridge.ai_translator.post_processor.llm_arbiter._get_prompts_dir",
                return_value=prompts_dir,
            ),
        ):
            ctx.esp_path = str(Path(data_dir) / "Demo.esp")
            result = _tool_run_postprocess(
                {"phases": ["refinement", "polish", "arbitration"]},
                ctx,
            )
            self.assertTrue(result.success, result.message)
            task_id = result.data["task_id"]
            deadline = time.monotonic() + 5
            while TaskManager().get_status(task_id)["status"] not in {"completed", "failed", "cancelled"}:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)

            from transbridge.smart_assistant.tools.tool_proofreader import get_last_report

            report = get_last_report()
            self.assertIsNotNone(report)
            self.assertTrue(report["report_file"].endswith(".xlsx"))
            self.assertEqual(
                {Path(path).suffix for path in report["report_files"]},
                {".json", ".csv", ".xlsx"},
            )
            self.assertTrue(all(Path(path).parent.name == "reports" for path in report["report_files"]))
            self.assertTrue(all(Path(path).is_file() for path in report["report_files"]))
            self.assertEqual(report["report_diagnostics"], [])
            self.assertEqual(report["strategy"], "strict")
            self.assertEqual(report["stages"], ["refinement", "polish", "arbitration"])
            self.assertEqual(report["log_dir"], "fixture-log")

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


if __name__ == "__main__":
    unittest.main()
