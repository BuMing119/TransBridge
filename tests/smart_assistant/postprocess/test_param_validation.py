"""Validate scope, intensity and strategy on the desktop polish task."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from tests.conftest import (
    make_entry,
    make_test_collection,
)
from tests.smart_assistant.postprocess.project_support import project_context
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.base import ExecutionContext


class TestStartPolishParamValidation(unittest.TestCase):
    """验收标准: start_polish 参数校验 + intensity 映射一致。"""

    def setUp(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_start_polish

        self.func = _tool_start_polish

    def test_c1_invalid_scope_rejected(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        result = self.func({"scope": "invalid_scope"}, ec)
        self.assertFalse(result.success)
        self.assertIn("无效 scope", result.message)

    def test_c2_scope_all_finds_entries_with_translations(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["entry_count"], 4)
        self.assertEqual(result.data["scope"], "all")

    def test_retired_stage_shortcuts_are_rejected_before_launch(self):
        ctx = project_context(self, collection=make_test_collection(10))
        for scope in ("passed", "has_issues"):
            with self.subTest(scope=scope):
                result = self.func({"scope": scope}, ExecutionContext(app_context=ctx))
                self.assertFalse(result.success)
                self.assertIn("set_scope", result.message)

    def test_c5_scope_no_matching_entries_returns_fail(self):
        entries = [make_entry(f"e{i}", original=f"orig {i}", translation="", stage=0) for i in range(3)]
        collection = TranslationEntryCollection(entries)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        result = self.func({"scope": "all"}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有可润色的词条", result.message)

    def test_c6_intensity_light_maps_to_light(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all", "intensity": "light"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["intensity"], "light")

    def test_c7_intensity_medium_maps_to_moderate(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all", "intensity": "medium"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["intensity"], "medium")

    def test_c8_intensity_heavy_maps_to_aggressive(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all", "intensity": "heavy"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["intensity"], "heavy")

    def test_c9_entry_ids_overrides_scope(self):
        collection = make_test_collection(10)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"entry_ids": ["entry_000", "entry_001"], "scope": "all"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["entry_count"], 1)

    def test_c10_empty_entry_ids_with_scope_default(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({}, ec)
        self.assertTrue(result.success)
        self.assertNotIn("scope", result.data)

    def test_c11_proofread_is_the_default_strategy(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["strategy"], "proofread")

    def test_c12_strict_strategy_is_accepted(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        with patch.object(threading.Thread, "start", return_value=None):
            result = self.func({"scope": "all", "strategy": "strict"}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["strategy"], "strict")

    def test_c13_invalid_strategy_is_rejected(self):
        collection = make_test_collection(5)
        ctx = project_context(self, collection=collection)
        ec = ExecutionContext(app_context=ctx)

        result = self.func({"scope": "all", "strategy": "unknown"}, ec)
        self.assertFalse(result.success)
        self.assertIn("无效 strategy", result.message)

    def test_c11_no_collection_rejected_by_decorator(self):
        ctx = project_context(self, collection=None)
        ec = ExecutionContext(app_context=ctx)

        result = self.func({}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有加载翻译集合", result.message)
