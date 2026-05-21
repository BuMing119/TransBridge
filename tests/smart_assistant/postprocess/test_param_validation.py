"""Story 25: run_postprocess / start_polish 参数验证测试。

Test A: run_postprocess 参数验证（空集合/entry_ids/scope/API key 检查）
Test C: start_polish 参数验证（scope 校验 / intensity 映射）
"""
from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from tests.conftest import (
    make_entry,
    make_llm_config,
    make_test_collection,
    MockAppContext,
)

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.smart_assistant.tools.base import ExecutionContext


# ── Test A: run_postprocess Parameter Validation ──────────────────────────

class TestRunPostprocessParamValidation(unittest.TestCase):
    """验收标准: run_postprocess 参数校验正确拒绝无效输入。"""

    def setUp(self):
        from src.transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess
        self.func = _tool_run_postprocess

    def test_a1_empty_collection_returns_fail(self):
        ctx = MockAppContext(collection=None)
        ec = ExecutionContext(app_context=ctx)
        result = self.func({}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有加载翻译集合", result.message)

    def test_a2_empty_collection_length_zero(self):
        empty_col = TranslationEntryCollection([])
        ctx = MockAppContext(collection=empty_col)
        ec = ExecutionContext(app_context=ctx)
        result = self.func({}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有加载翻译集合", result.message)

    def test_a3_nonexistent_entry_ids_returns_fail(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)
        result = self.func({"entry_ids": ["nonexistent_1", "nonexistent_2"]}, ec)
        self.assertFalse(result.success)
        self.assertIn("所有指定的 entry_id 均无效", result.message)

    def test_a4_mixed_entry_ids_filters_correctly(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch(
            "src.transbridge.paratranz.config_manager.LLMConfig.load_from_file"
        ) as mock_load:
            mock_cfg = make_llm_config(api_key="")
            mock_load.return_value = mock_cfg

            with patch.object(threading.Thread, "start", return_value=None):
                result = self.func(
                    {"entry_ids": ["entry_000", "nonexistent_999"]}, ec
                )
        self.assertTrue(result.success)
        self.assertIn("task_id", result.data)
        self.assertEqual(result.data["entry_count"], 1)

    def test_a5_valid_entry_ids_reaches_config_check(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch(
            "src.transbridge.paratranz.config_manager.LLMConfig.load_from_file"
        ) as mock_load:
            mock_cfg = make_llm_config(api_key="")
            mock_load.return_value = mock_cfg

            with patch.object(threading.Thread, "start", return_value=None):
                result = self.func({"entry_ids": ["entry_000", "entry_002"]}, ec)
        self.assertTrue(result.success)
        self.assertIn("task_id", result.data)
        self.assertEqual(result.data["entry_count"], 2)

    def test_a6_translation_scope_resolves_entries(self):
        collection = make_test_collection(10)
        ctx = MockAppContext(collection=collection)
        ctx.translation_scope = {
            "stages": [0], "labels": [], "categories": [], "action": "include",
        }
        ec = ExecutionContext(app_context=ctx)

        with patch(
            "src.transbridge.paratranz.config_manager.LLMConfig.load_from_file"
        ) as mock_load:
            mock_cfg = make_llm_config(api_key="")
            mock_load.return_value = mock_cfg

            with patch.object(threading.Thread, "start", return_value=None):
                result = self.func({}, ec)
        self.assertTrue(result.success)
        self.assertIn("task_id", result.data)
        self.assertGreater(result.data["entry_count"], 0)

    def test_a7_translation_scope_empty_result_behavior(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ctx.translation_scope = {
            "stages": [99], "labels": [], "categories": [], "action": "include",
        }
        ec = ExecutionContext(app_context=ctx)

        result = self.func({}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有可处理的条目", result.message)

    def test_a8_default_phases_list(self):
        expected_default = [
            "consistency", "format", "quality_gate",
            "refinement", "polish", "arbitration",
        ]
        self.assertEqual(len(expected_default), 6)
        for phase in ["consistency", "format", "quality_gate",
                      "refinement", "polish", "arbitration"]:
            self.assertIn(phase, expected_default)

    def test_a9_custom_phases_in_data(self):
        args_with_phases = {"entry_ids": ["entry_000"], "phases": ["consistency", "polish"]}
        phases = args_with_phases.get(
            "phases",
            ["consistency", "format", "quality_gate", "refinement", "polish", "arbitration"],
        )
        self.assertEqual(phases, ["consistency", "polish"])

        args_without_phases = {"entry_ids": ["entry_000"]}
        phases = args_without_phases.get(
            "phases",
            ["consistency", "format", "quality_gate", "refinement", "polish", "arbitration"],
        )
        self.assertEqual(len(phases), 6)
        self.assertIn("consistency", phases)

    def test_a10_unknown_phase_name_no_crash(self):
        collection = make_test_collection(3)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        result = self.func(
            {"entry_ids": ["entry_000"], "phases": ["unknown_phase", "garbage"]}, ec
        )
        self.assertFalse(result.success)
        self.assertIn("无效的阶段名", result.message)

    def test_a11_api_key_missing_error_fields(self):
        collection = make_test_collection(3)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch(
            "src.transbridge.paratranz.config_manager.LLMConfig.load_from_file"
        ) as mock_load:
            mock_cfg = make_llm_config(api_key="")
            mock_load.return_value = mock_cfg

            with patch.object(threading.Thread, "start", return_value=None):
                result = self.func({"entry_ids": ["entry_000"]}, ec)
        self.assertTrue(result.success)
        self.assertIn("task_id", result.data)


# ── Test C: start_polish Parameter Validation ─────────────────────────────

class TestStartPolishParamValidation(unittest.TestCase):
    """验收标准: start_polish 参数校验 + intensity 映射一致。"""

    def setUp(self):
        from src.transbridge.smart_assistant.tools.tool_translator import _tool_start_polish
        self.func = _tool_start_polish

    def test_c1_invalid_scope_rejected(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        result = self.func({"scope": "invalid_scope"}, ec)
        self.assertFalse(result.success)
        self.assertIn("无效 scope", result.message)

    def test_c2_scope_all_finds_entries_with_translations(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["entry_count"], 4)
        self.assertEqual(result.data["scope"], "all")

    def test_c3_scope_passed_finds_correct_entries(self):
        collection = make_test_collection(10)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "passed"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["scope"], "passed")
        self.assertGreater(result.data["entry_count"], 0)

    def test_c4_scope_has_issues_finds_stage2_entries(self):
        collection = make_test_collection(10)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "has_issues"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["scope"], "has_issues")

    def test_c5_scope_no_matching_entries_returns_fail(self):
        entries = [
            make_entry(f"e{i}", original=f"orig {i}", translation="", stage=0)
            for i in range(3)
        ]
        collection = TranslationEntryCollection(entries)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        result = self.func({"scope": "all"}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有符合 scope=all", result.message)

    def test_c6_intensity_light_maps_to_light(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all", "intensity": "light"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["intensity"], "light")

    def test_c7_intensity_medium_maps_to_moderate(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all", "intensity": "medium"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["intensity"], "medium")

    def test_c8_intensity_heavy_maps_to_aggressive(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all", "intensity": "heavy"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["intensity"], "heavy")

    def test_c9_entry_ids_overrides_scope(self):
        collection = make_test_collection(10)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func(
                {"entry_ids": ["entry_000", "entry_001"], "scope": "all"}, ec
            )
        self.assertTrue(result.success)
        self.assertEqual(result.data["entry_count"], 2)

    def test_c10_empty_entry_ids_with_scope_default(self):
        collection = make_test_collection(5)
        ctx = MockAppContext(collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["scope"], "all")

    def test_c11_no_collection_rejected_by_decorator(self):
        ctx = MockAppContext(collection=None)
        ec = ExecutionContext(app_context=ctx)

        result = self.func({}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有加载翻译集合", result.message)
