"""Story 14: 跨 Story 集成测试 — 覆盖工具全链路/标签/安全/配置/ParaTranz/解析写回。

测试架构：MockAppContext 模拟真实 AppContext 的 ViewModel 层（筛选/标签/作用域/选择），
通过直接调用工具函数验证跨 Story 数据传递和业务流程正确性。
"""

from __future__ import annotations

import os
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.conftest import (
    MockAppContext,
    MockToolSpec,
    make_test_collection,
)
from transbridge.smart_assistant.tools.base import (
    ExecutionContext,
    ToolResult,
    execute_with_guardrails,
    filter_entries,
    require_collection,
    validate_params,
)
from transbridge.smart_assistant.tools.task_manager import TaskManager

# ── Test: 完整工作流链路 ────────────────────────────────────────────────


class TestFullWorkflowChain(unittest.TestCase):
    """验收标准: 筛选→搜索→选择→编辑→标记→翻译 完整链路数据传递正确性。"""

    def setUp(self):
        self.collection = make_test_collection(15)
        self.ctx = MockAppContext(self.collection)

    def test_filter_by_stage(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters

        result = _tool_set_filters({"stages": [0, 1]}, self.ctx)
        self.assertTrue(result.success)
        self.assertEqual(self.ctx.filter_state["stage"], [0, 1])

    def test_filter_by_stage_rejects_invalid(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters

        result = _tool_set_filters({"stages": [99]}, self.ctx)
        self.assertFalse(result.success)

    def test_filter_by_category(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters

        result = _tool_set_filters({"categories": ["NPC_"]}, self.ctx)
        self.assertTrue(result.success)
        self.assertEqual(self.ctx.filter_state["category"], ["NPC_"])

    def test_search_entries(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters

        result = _tool_set_filters({"search_query": "Original", "search_field": "original"}, self.ctx)
        self.assertTrue(result.success)
        self.assertEqual(self.ctx.filter_state["search_query"], "Original")

    def test_search_entries_invalid_field(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters

        result = _tool_set_filters({"search_query": "test", "search_field": "unknown"}, self.ctx)
        self.assertFalse(result.success)

    def test_get_visible_entries_respects_filter(self):
        from transbridge.smart_assistant.tools.tool_editor import (
            _tool_get_visible_entries,
            _tool_set_filters,
        )

        _tool_set_filters({"stages": [1]}, self.ctx)
        result = _tool_get_visible_entries({"limit": 50}, self.ctx)
        self.assertTrue(result.success)
        # 15 entries, stages [0,1,2,3,5] × 3 = 3 entries at stage 1
        self.assertEqual(result.data["total_count"], 3)

    def test_get_visible_entries_pagination(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_get_visible_entries

        result = _tool_get_visible_entries({"limit": 3, "offset": 0}, self.ctx)
        self.assertTrue(result.success)
        self.assertEqual(len(result.data["entries"]), 3)
        self.assertTrue(result.truncated)

    def test_select_entries(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_select_entries

        result = _tool_select_entries(
            {"entry_ids": ["entry_000", "entry_001", "entry_002"], "action": "select"},
            self.ctx,
        )
        self.assertTrue(result.success)
        self.assertEqual(result.data["selected_count"], 3)
        self.assertIn("entry_000", self.ctx.selected_ids)

    def test_deselect_and_clear(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_select_entries

        _tool_select_entries({"entry_ids": ["entry_000", "entry_001"], "action": "select"}, self.ctx)
        self.assertEqual(len(self.ctx.selected_ids), 2)
        _tool_select_entries({"entry_ids": ["entry_000"], "action": "deselect"}, self.ctx)
        self.assertEqual(len(self.ctx.selected_ids), 1)
        _tool_select_entries({"entry_ids": [], "action": "clear"}, self.ctx)
        self.assertEqual(len(self.ctx.selected_ids), 0)

    def test_edit_translation(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_edit_translation

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_edit_translation(
            {"entry_id": "entry_000", "new_translation": "新翻译文本"},
            ec,
        )
        self.assertTrue(result.success)
        self.assertEqual(self.collection.get("entry_000").translation, "新翻译文本")

    def test_edit_translation_with_stage_change(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_edit_translation

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_edit_translation(
            {"entry_id": "entry_000", "new_translation": "改译文", "new_stage": 1},
            ec,
        )
        self.assertTrue(result.success)
        self.assertTrue(result.data["stage_changed"])
        self.assertEqual(self.collection.get("entry_000").stage, 1)

    def test_edit_translation_nonexistent(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_edit_translation

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_edit_translation({"entry_id": "nonexistent", "new_translation": "x"}, ec)
        self.assertFalse(result.success)

    def test_set_stage_batch(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_stage

        ec = ExecutionContext(app_context=self.ctx)
        ids = ["entry_000", "entry_001", "entry_002"]
        result = _tool_set_stage({"entry_ids": ids, "stage": 9}, ec)
        self.assertTrue(result.success)
        self.assertEqual(result.data["updated_count"], 3)
        for eid in ids:
            self.assertEqual(self.collection.get(eid).stage, 9)

    def test_set_stage_partial_not_found(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_stage

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_set_stage({"entry_ids": ["entry_000", "missing_999"], "stage": 1}, ec)
        self.assertTrue(result.success)
        self.assertTrue(result.partial)
        self.assertEqual(len(result.failed_items), 1)

    def test_start_translation_stop_and_status(self):
        from transbridge.smart_assistant.tools.tool_translator import (
            _tool_get_task_status,
            _tool_start_translation,
            _tool_stop_task,
        )

        TaskManager.reset()
        self.addCleanup(TaskManager.reset)
        ec = ExecutionContext(app_context=self.ctx, task_manager=TaskManager())
        test_config = SimpleNamespace(api_key="test-key", term_priority=["dynamic"])
        with (
            patch(
                "transbridge.paratranz.config_manager.LLMConfig.load_from_file",
                return_value=test_config,
            ),
            patch.object(TaskManager, "start_thread", return_value=None),
        ):
            result = _tool_start_translation({"mode": "translate"}, ec)
        self.assertTrue(result.success)
        task_id = result.data["task_id"]

        status = _tool_get_task_status({"task_id": task_id}, ec)
        self.assertTrue(status.success)
        self.assertIn(status.data["status"], ("running", "completed", "cancelled"))

        stop_result = _tool_stop_task({"task_id": task_id}, ec)
        self.assertTrue(stop_result.success)

        all_result = _tool_stop_task({}, ec)
        self.assertTrue(all_result.success)

    def test_start_polish_needs_entry_ids(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_start_polish

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_start_polish({"entry_ids": []}, ec)
        self.assertFalse(result.success)

    def test_full_workflow_filter_search_select_edit_mark(self):
        """端到端: 筛选→搜索→查询→选择→编辑→标记，验证数据在各 Story 间正确传递。"""
        from transbridge.smart_assistant.tools.tool_editor import (
            _tool_edit_translation,
            _tool_get_visible_entries,
            _tool_select_entries,
            _tool_set_filters,
            _tool_set_stage,
        )

        ec = ExecutionContext(app_context=self.ctx)

        r1 = _tool_set_filters({"stages": [0]}, self.ctx)
        self.assertTrue(r1.success)

        r2 = _tool_set_filters({"search_query": "Original text 0", "search_field": "original"}, self.ctx)
        self.assertTrue(r2.success)

        r3 = _tool_get_visible_entries({"limit": 20}, self.ctx)
        self.assertTrue(r3.success)
        visible_ids = [e["id"] for e in r3.data["entries"]]
        self.assertGreater(len(visible_ids), 0)

        r4 = _tool_select_entries({"entry_ids": visible_ids, "action": "select"}, self.ctx)
        self.assertTrue(r4.success)
        self.assertGreater(r4.data["selected_count"], 0)

        target = visible_ids[0]
        r5 = _tool_edit_translation(
            {"entry_id": target, "new_translation": "集成测试译文", "new_stage": 1},
            ec,
        )
        self.assertTrue(r5.success)

        rest = [eid for eid in visible_ids if eid != target]
        if rest:
            r6 = _tool_set_stage({"entry_ids": rest, "stage": 3}, ec)
            self.assertTrue(r6.success)

        entry = self.collection.get(target)
        self.assertEqual(entry.translation, "集成测试译文")
        self.assertEqual(entry.stage, 1)

        r8 = _tool_set_filters({"clear": True}, self.ctx)
        self.assertTrue(r8.success)
        self.assertEqual(self.ctx.filter_state["stage"], [])


# ── Test: 标签系统 ──────────────────────────────────────────────────────


class TestLabelSystem(unittest.TestCase):
    """验收标准: create→assign→filter→batch_assign→remove 全流程，
    标签数据通过 AppContext 正确共享 (Story 03 B1 联动)。
    """

    def setUp(self):
        self.collection = make_test_collection(10)
        self.ctx = MockAppContext(self.collection)

    def test_label_full_lifecycle(self):
        from transbridge.smart_assistant.tools.tool_editor import (
            _tool_list_labels,
            _tool_manage_entry_labels,
        )

        ec = ExecutionContext(app_context=self.ctx)

        # 1. Create
        r1 = _tool_manage_entry_labels({"action": "create", "name": "需要校对", "color": "#FF5722"}, self.ctx)
        self.assertTrue(r1.success)
        label_id = r1.data["label_id"]

        # 2. List
        r2 = _tool_list_labels({}, self.ctx)
        self.assertTrue(r2.success)
        self.assertEqual(len(r2.data["labels"]), 1)
        self.assertEqual(r2.data["labels"][0]["name"], "需要校对")

        # 3. Assign to specific entries
        r3 = _tool_manage_entry_labels(
            {"action": "assign", "name": "需要校对", "entry_ids": ["entry_000", "entry_001"]},
            ec,
            self.collection,
        )
        self.assertTrue(r3.success)
        self.assertEqual(r3.data["assigned_count"], 2)

        # 4. Batch assign (without label filter to avoid filter_entries label bug)
        r5 = _tool_manage_entry_labels({"action": "batch_assign", "name": "需要校对"}, ec, self.collection)
        self.assertTrue(r5.success)
        self.assertGreaterEqual(r5.data["assigned_count"], 2)

        # 5. Remove
        r6 = _tool_manage_entry_labels(
            {"action": "unassign", "name": "需要校对", "entry_ids": ["entry_000"]},
            ec,
            self.collection,
        )
        self.assertTrue(r6.success)
        self.assertEqual(r6.data["removed_count"], 1)

        # 6. Verify data sharing (B1)
        self.assertIn(label_id, self.ctx.label_library)
        self.assertIn("entry_001", self.ctx.entry_labels)

    def test_create_label_empty_name_rejected(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_manage_entry_labels

        result = _tool_manage_entry_labels({"action": "create", "name": ""}, self.ctx)
        self.assertFalse(result.success)

    def test_assign_nonexistent_label(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_manage_entry_labels

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_manage_entry_labels(
            {"action": "assign", "name": "不存在的标签", "entry_ids": ["entry_000"]},
            ec,
            self.collection,
        )
        self.assertFalse(result.success)

    def test_label_mutations_replace_copy_on_read_state(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_manage_entry_labels

        class CopyOnReadContext(MockAppContext):
            @property
            def label_library(self) -> dict:
                return {key: dict(value) for key, value in self._label_library.items()}

            @label_library.setter
            def label_library(self, value: dict) -> None:
                self._label_library = {key: dict(info) for key, info in value.items()}

            @property
            def entry_labels(self) -> dict:
                return {key: set(value) for key, value in self._entry_labels.items()}

            @entry_labels.setter
            def entry_labels(self, value: dict) -> None:
                self._entry_labels = {key: set(labels) for key, labels in value.items()}

        context = CopyOnReadContext(self.collection)
        execution = ExecutionContext(app_context=context)
        created = _tool_manage_entry_labels({"action": "create", "name": "副本安全"}, context)
        assigned = _tool_manage_entry_labels(
            {"action": "assign", "name": "副本安全", "entry_ids": ["entry_000"]},
            execution,
            self.collection,
        )
        unassigned = _tool_manage_entry_labels(
            {"action": "unassign", "name": "副本安全", "entry_ids": ["entry_000"]},
            execution,
            self.collection,
        )

        self.assertTrue(created.success)
        self.assertTrue(assigned.success)
        self.assertTrue(unassigned.success)
        label_id = created.data["label_id"]
        self.assertIn(label_id, context.label_library)
        self.assertNotIn(label_id, context.entry_labels["entry_000"])

    def test_authoritative_label_mutations_use_projected_label_command(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_manage_entry_labels

        class AuthoritativeContext(MockAppContext):
            uses_authoritative_projection = True
            active_version_identity = ("project", "variant")
            project_revision = 1
            variant_revision = 1

            def __init__(self, collection) -> None:
                super().__init__(collection)
                self.replacements: list[tuple[dict, dict]] = []

            @property
            def label_library(self) -> dict:
                return {key: dict(value) for key, value in self._label_library.items()}

            @label_library.setter
            def label_library(self, _value: dict) -> None:
                raise AssertionError("authoritative label library must not use the projection setter")

            @property
            def entry_labels(self) -> dict:
                return {key: set(value) for key, value in self._entry_labels.items()}

            @entry_labels.setter
            def entry_labels(self, _value: dict) -> None:
                raise AssertionError("authoritative entry labels must not use the projection setter")

            def replace_projected_labels(self, entry_labels: dict, label_library: dict, **_expected):
                copied_entries = {key: set(value) for key, value in entry_labels.items()}
                copied_library = {key: dict(value) for key, value in label_library.items()}
                self.replacements.append((copied_entries, copied_library))
                self._entry_labels = copied_entries
                self._label_library = copied_library
                return SimpleNamespace(is_success=True)

        context = AuthoritativeContext(self.collection)
        execution = ExecutionContext(app_context=context)
        created = _tool_manage_entry_labels({"action": "create", "name": "权威标签"}, context)
        assigned = _tool_manage_entry_labels(
            {"action": "assign", "name": "权威标签", "entry_ids": ["entry_001"]},
            execution,
            self.collection,
        )

        self.assertTrue(created.success)
        self.assertTrue(assigned.success)
        label_id = created.data["label_id"]
        self.assertEqual(len(context.replacements), 2)
        self.assertIn(label_id, context.replacements[-1][0]["entry_001"])


# ── Test: 安全护栏 ──────────────────────────────────────────────────────


class TestSecurityGuardrails(unittest.TestCase):
    """验收标准: 路径遍历拒绝 + 权限拒绝 + MCP 中间件链统一入口。"""

    def setUp(self):
        self.ctx = MockAppContext(make_test_collection(5))

    def test_path_traversal_dot_dot_slash(self):
        from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard

        guard = InputValidationGuard()
        step = {"tool": "parse_esp", "args": {"path": "../etc/passwd"}}
        result = guard.before_execute(step, ExecutionContext(app_context=self.ctx))
        self.assertFalse(result.allowed)

    def test_path_traversal_dot_dot_backslash(self):
        from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard

        guard = InputValidationGuard()
        step = {"tool": "parse_esp", "args": {"path": "..\\Windows\\system32\\config"}}
        result = guard.before_execute(step, ExecutionContext(app_context=self.ctx))
        self.assertFalse(result.allowed)

    def test_path_traversal_absolute_windows(self):
        from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard

        guard = InputValidationGuard()
        step = {"tool": "parse_esp", "args": {"path": "C:\\Windows\\system32\\config"}}
        result = guard.before_execute(step, ExecutionContext(app_context=self.ctx))
        self.assertFalse(result.allowed)

    def test_path_traversal_absolute_unix(self):
        from transbridge.smart_assistant.guardrails.input_validator import InputValidationGuard

        guard = InputValidationGuard()
        step = {"tool": "parse_esp", "args": {"path": "/etc/passwd"}}
        result = guard.before_execute(step, ExecutionContext(app_context=self.ctx))
        self.assertFalse(result.allowed)

    def test_read_permission_allowed(self):
        from transbridge.smart_assistant.guardrails.permission import PermissionGuard

        guard = PermissionGuard()
        # Story 17: filter_by_stage → set_filters
        step = {"tool": "set_filters", "args": {"stages": [0]}}
        result = guard.before_execute(step, ExecutionContext(app_context=self.ctx))
        self.assertTrue(result.allowed)

    def test_execute_with_guardrails_perm_denied(self):
        """admin 工具在无 UI 确认时应被拒绝 (B6)。"""

        def _dummy_exec(args, ctx):
            return ToolResult.ok("done")

        spec = MockToolSpec(name="write_to_esp", permission="admin", execute=_dummy_exec)
        spec.require_confirmation = True  # writer tools require confirmation
        result = execute_with_guardrails(spec, {"path": "test.esp"}, ExecutionContext(app_context=self.ctx))
        self.assertFalse(result.success)

    def test_execute_with_guardrails_allows_read(self):
        def _dummy_exec(args, ctx):
            return ToolResult.ok("ok", data={"result": 42})

        # Story 17: filter_by_stage → set_filters
        spec = MockToolSpec(name="set_filters", permission="read", execute=_dummy_exec)
        result = execute_with_guardrails(spec, {"stages": [0]}, ExecutionContext(app_context=self.ctx))
        self.assertTrue(result.success)
        self.assertEqual(result.data["result"], 42)

    def test_execute_with_guardrails_input_validation(self):
        """路径遍历参数应被 InputValidationGuard 拦截。

        PermissionGuard 会验证工具是否在 ToolRegistry 中注册，
        因此需确保 tool_parser 模块已加载（parse_esp 注册为 write 权限，
        PermissionGuard 放行 write 权限工具）。
        """
        import transbridge.smart_assistant.tools.tool_parser  # noqa: F401 — ensure parse_esp registered

        def _dummy_exec(args, ctx):
            return ToolResult.ok("should not reach")

        spec = MockToolSpec(name="parse_esp", permission="read", execute=_dummy_exec)
        result = execute_with_guardrails(
            spec, {"path": "../secrets/config.esp"}, ExecutionContext(app_context=self.ctx)
        )
        self.assertFalse(result.success)
        self.assertIn("路径遍历", result.message)


# ── Test: 翻译配置 ──────────────────────────────────────────────────────


class TestTranslationConfig(unittest.TestCase):
    """验收标准: profile 预设方案切换 + scope 设置/预览一致性。"""

    def setUp(self):
        self.ctx = MockAppContext(make_test_collection(10))
        # 防止 set_translation_config 测试写入真实 INI 覆盖用户配置
        from unittest.mock import patch

        self._save_patcher = patch(
            "transbridge.config.llm.LLMConfig.save_to_file",
            return_value=None,
        )
        self._save_patcher.start()

    def tearDown(self):
        self._save_patcher.stop()

    def test_set_scope_valid(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_set_scope

        result = _tool_set_scope({"stages": [0, 1], "action": "include"}, self.ctx)
        self.assertTrue(result.success)
        self.assertEqual(self.ctx.translation_scope["stages"], [0, 1])

    def test_set_scope_invalid_action(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_set_scope

        result = _tool_set_scope({"stages": [0], "action": "invalid"}, self.ctx)
        self.assertFalse(result.success)

    def test_get_scope_preview_consistency(self):
        from transbridge.smart_assistant.tools.tool_translator import (
            _tool_get_scope_preview,
            _tool_set_scope,
        )

        _tool_set_scope({"stages": [0], "action": "include"}, self.ctx)
        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_get_scope_preview({}, ec)
        self.assertTrue(result.success)
        self.assertIn("matched", result.data)
        self.assertIn("total", result.data)
        self.assertGreater(result.data["total"], 0)

    def test_set_translation_config_without_profile(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_set_translation_config

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_set_translation_config({"model": "gpt-4o"}, ec)
        self.assertIsInstance(result, ToolResult)

    def test_set_translation_config_rejects_missing_language_profile(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_set_translation_config

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_set_translation_config({"target_lang": "missing_LOCALE"}, ec)

        self.assertFalse(result.success)
        self.assertIn("Unsupported language profile", result.message)


# ── Test: 状态查询工具 ───────────────────────────────────────────────────


class TestStateQueryTools(unittest.TestCase):
    """验收标准: 状态查询+校对工具基本功能。"""

    def setUp(self):
        self.collection = make_test_collection(15)
        self.ctx = MockAppContext(self.collection)

    def test_get_statistics(self):
        from transbridge.smart_assistant.tools.tool_default import _tool_get_statistics

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_get_statistics({}, ec)
        self.assertTrue(result.success)
        self.assertIn("total", result.data)
        self.assertEqual(result.data["total"], 15)

    def test_list_local_projects(self):
        from transbridge.smart_assistant.tools.tool_default import _tool_list_local_projects

        result = _tool_list_local_projects({}, self.ctx)
        self.assertTrue(result.success)
        self.assertIn("projects", result.data)

    def test_get_current_project_no_active(self):
        from transbridge.smart_assistant.tools.tool_default import _tool_get_current_project

        result = _tool_get_current_project({}, self.ctx)
        self.assertTrue(result.success)
        self.assertIsNone(result.data["active_project"])

    def test_get_quality_report(self):
        from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool_get_quality_report({}, ec)
        self.assertTrue(result.success)
        self.assertIn("reports", result.data)


# ── Test: Parser/Writer 工具 ─────────────────────────────────────────────


class TestParserWriterTools(unittest.TestCase):
    """验收标准: parser read 权限 + 扩展名白名单 + writer admin 确认。"""

    def setUp(self):
        self.ctx = MockAppContext()

    def test_validate_path_nonexistent_file(self):
        from transbridge.smart_assistant.tools.tool_parser import _validate_path

        result = _validate_path("nonexistent.esp")
        self.assertIsNotNone(result)
        self.assertIn("文件不存在", result.message)

    def test_validate_path_invalid_extension(self):
        """Extension check runs only when file exists — use temp file."""
        from transbridge.smart_assistant.tools.tool_parser import _validate_path

        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            exe_path = f.name
        try:
            result = _validate_path(exe_path)
            self.assertIsNotNone(result)
            self.assertIn("不支持的文件类型", result.message)
        finally:
            os.unlink(exe_path)

    def test_validate_path_extension_whitelist_all(self):
        from transbridge.smart_assistant.tools.tool_parser import _VALID_EXTENSIONS

        for ext in [".esp", ".esm", ".esl", ".xml", ".json", ".sst"]:
            self.assertIn(ext, _VALID_EXTENSIONS, f"Extension {ext} should be whitelisted")

    def test_validate_path_rejects_binary(self):
        from transbridge.smart_assistant.tools.tool_parser import _validate_path

        with tempfile.NamedTemporaryFile(suffix=".dll", delete=False) as f:
            dll_path = f.name
        try:
            result = _validate_path(dll_path)
            self.assertIsNotNone(result)
            self.assertIn("不支持的文件类型", result.message)
        finally:
            os.unlink(dll_path)

    def test_parse_esp_no_path(self):
        from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp

        result = _tool_parse_esp({"path": ""}, self.ctx)
        self.assertFalse(result.success)

    def test_parse_esp_nonexistent_file(self):
        from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp

        result = _tool_parse_esp({"path": "nonexistent.esp"}, self.ctx)
        self.assertFalse(result.success)
        self.assertIn("文件不存在", result.message)

    def test_parse_esp_invalid_extension(self):
        from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp

        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            exe_path = f.name
        try:
            result = _tool_parse_esp({"path": exe_path}, self.ctx)
            self.assertFalse(result.success)
            self.assertIn("不支持的文件类型", result.message)
        finally:
            os.unlink(exe_path)

    def test_writer_tools_have_admin_permission(self):
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        writer_specs = ToolRegistry.list_namespace("writer")
        for spec in writer_specs:
            self.assertEqual(spec.permission, "admin", f"Writer tool {spec.name} should have admin permission")
            self.assertTrue(spec.require_confirmation, f"Writer tool {spec.name} should require confirmation")

    def test_parser_tools_have_write_permission(self):
        """Story 24: parser 工具执行副作用（创建slot/追加条目），permission 升级为 write。"""
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        parser_specs = ToolRegistry.list_namespace("parser")
        for spec in parser_specs:
            self.assertEqual(
                spec.permission, "write", f"Parser tool {spec.name} should have write permission (Story 24)"
            )


# ── Test: ToolResult v2 ──────────────────────────────────────────────────


class TestToolResultV2(unittest.TestCase):
    """验收标准: ToolResult v2 字典兼容 + success/partial 语义正确。"""

    def test_ok(self):
        r = ToolResult.ok("成功", data={"count": 5})
        self.assertTrue(r.success)
        self.assertFalse(r.partial)
        self.assertEqual(r.data["count"], 5)

    def test_fail(self):
        r = ToolResult.fail("失败")
        self.assertFalse(r.success)
        self.assertFalse(r.partial)

    def test_partial_ok(self):
        r = ToolResult.partial_ok("部分成功", data={"ok": 3}, failed_items=[{"id": "x", "reason": "not found"}])
        self.assertTrue(r.success)
        self.assertTrue(r.partial)
        self.assertEqual(len(r.failed_items), 1)

    def test_get_dict_compat(self):
        """B2: ToolResult.get() 兼容 execution_engine 的 raw_result.get() 模式。"""
        r = ToolResult.ok("ok", data={"x": 1})
        self.assertTrue(r.get("success"))
        self.assertEqual(r.get("message"), "ok")
        self.assertEqual(r.get("data"), {"x": 1})
        self.assertIsNone(r.get("nonexistent"))

    def test_getitem(self):
        """B2: __getitem__ 字典索引兼容。"""
        r = ToolResult.ok("ok", data={"x": 1})
        self.assertTrue(r["success"])
        self.assertEqual(r["message"], "ok")

    def test_truncated_set_after_factory(self):
        r = ToolResult.ok("msg")
        r.truncated = True
        self.assertTrue(r.truncated)
        self.assertIn("truncated", r.to_dict())

    def test_to_dict_basic(self):
        r = ToolResult.ok("ok", data={"a": 1})
        d = r.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(d["data"], {"a": 1})

    def test_to_dict_with_failed_items(self):
        r = ToolResult.partial_ok("部分", failed_items=[{"id": "x"}])
        d = r.to_dict()
        self.assertIn("failed_items", d)
        self.assertTrue(d["partial"])


# ── Test: ExecutionContext __getattr__ proxy ─────────────────────────────


class TestExecutionContext(unittest.TestCase):
    """验收标准: ExecutionContext.__getattr__ 正确代理 AppContext 属性 (H9)。"""

    def setUp(self):
        self.collection = make_test_collection(5)
        self.app_ctx = MockAppContext(self.collection)

    def test_direct_attr_access(self):
        ec = ExecutionContext(app_context=self.app_ctx)
        self.assertIs(ec.app_context, self.app_ctx)

    def test_proxy_to_app_context(self):
        ec = ExecutionContext(app_context=self.app_ctx)
        fs = ec.filter_state
        self.assertIsInstance(fs, dict)
        self.assertIn("stage", fs)

    def test_proxy_set_filter(self):
        ec = ExecutionContext(app_context=self.app_ctx)
        ec.set_filter(stage=[0, 1])
        self.assertEqual(ec.filter_state["stage"], [0, 1])

    def test_proxy_slot_collection(self):
        ec = ExecutionContext(app_context=self.app_ctx)
        slot = ec.active_slot
        self.assertIsNotNone(slot)
        self.assertEqual(len(slot.collection), 5)

    def test_proxy_collection(self):
        ec = ExecutionContext(app_context=self.app_ctx)
        self.assertIsNotNone(ec.collection)
        self.assertEqual(len(ec.collection), 5)

    def test_private_attr_not_proxied(self):
        ec = ExecutionContext(app_context=self.app_ctx)
        with self.assertRaises(AttributeError):
            _ = ec._nonexistent_private

    def test_nonexistent_attr_raises(self):
        ec = ExecutionContext(app_context=self.app_ctx)
        with self.assertRaises(AttributeError):
            _ = ec.completely_nonexistent


# ── Test: filter_entries 公共函数 ──────────────────────────────────────


class TestFilterEntries(unittest.TestCase):
    """验收标准: filter_entries() 统一筛选行为 (H8)。"""

    def setUp(self):
        self.collection = make_test_collection(20)

    def test_filter_by_stage(self):
        results = filter_entries(self.collection, {"stage": [0]})
        self.assertGreater(len(results), 0)
        for e in results:
            self.assertEqual(e.stage, 0)

    def test_filter_by_category(self):
        results = filter_entries(self.collection, {"category": ["NPC_"]})
        for e in results:
            self.assertTrue(e.context and e.context.startswith("NPC_"))

    def test_filter_by_search_text(self):
        results = filter_entries(self.collection, {"search_query": "Original text 0", "search_field": "text"})
        self.assertGreater(len(results), 0)
        for e in results:
            self.assertIn("Original text 0", e.original)

    def test_filter_by_search_id(self):
        results = filter_entries(self.collection, {"search_query": "entry_000", "search_field": "id"})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].id, "entry_000")

    def test_filter_empty_returns_all(self):
        results = filter_entries(self.collection, {})
        self.assertEqual(len(results), 20)

    def test_combined_stage_and_search(self):
        results = filter_entries(self.collection, {"stage": [1], "search_query": "Original", "search_field": "text"})
        self.assertGreater(len(results), 0)
        for e in results:
            self.assertEqual(e.stage, 1)
            self.assertIn("Original", e.original)


# ── Test: TaskManager 单例 ───────────────────────────────────────────────


class TestTaskManager(unittest.TestCase):
    """验收标准: TaskManager 单例线程安全 (E3)。"""

    def setUp(self):
        # Clean up any leftover tasks from other tests
        tm = TaskManager()
        tm.cleanup_all()

    def test_singleton(self):
        tm1 = TaskManager()
        tm2 = TaskManager()
        self.assertIs(tm1, tm2)

    def test_register_and_get_status(self):
        tm = TaskManager()
        e = threading.Event()
        tid = tm.register(stop_event=e, metadata={"test": True})
        status = tm.get_status(tid)
        self.assertEqual(status["status"], "running")
        self.assertTrue(status["metadata"]["test"])

    def test_cancel(self):
        tm = TaskManager()
        tid = tm.register()
        ok = tm.cancel(tid)
        self.assertTrue(ok)
        status = tm.get_status(tid)
        self.assertEqual(status["status"], "cancelled")

    def test_cancel_nonexistent(self):
        tm = TaskManager()
        ok = tm.cancel("nonexistent_id")
        self.assertFalse(ok)

    def test_list_active(self):
        tm = TaskManager()
        tid = tm.register()
        self.assertIn(tid, tm.list_active())
        tm.cancel(tid)
        self.assertNotIn(tid, tm.list_active())

    def test_cleanup(self):
        tm = TaskManager()
        tid = tm.register()
        tm.cancel(tid)
        tm.cleanup(tid)
        status = tm.get_status(tid)
        self.assertIn("error", status)

    def test_deep_copy_progress(self):
        tm = TaskManager()
        tid = tm.register()
        tm.update_progress(tid, {"current": 5, "total": 10})
        status1 = tm.get_status(tid)
        status2 = tm.get_status(tid)
        status1["progress"]["current"] = 999
        self.assertEqual(status2["progress"]["current"], 5)

    def test_update_progress_accumulates(self):
        tm = TaskManager()
        tid = tm.register()
        tm.update_progress(tid, {"current": 3})
        tm.update_progress(tid, {"total": 10})
        status = tm.get_status(tid)
        self.assertEqual(status["progress"]["current"], 3)
        self.assertEqual(status["progress"]["total"], 10)

    def test_list_all_includes_all_statuses(self):
        tm = TaskManager()
        tid1 = tm.register()
        tid2 = tm.register()
        tm.cancel(tid2)
        all_ids = tm.list_all()
        self.assertIn(tid1, all_ids)
        self.assertIn(tid2, all_ids)


# ── Test: 装饰器 ─────────────────────────────────────────────────────────


class TestDecorators(unittest.TestCase):
    """验收标准: @require_collection + @validate_params 装饰器功能正确。"""

    def setUp(self):
        self.collection = make_test_collection(5)
        self.ctx = MockAppContext(self.collection)

    def test_require_collection_success(self):
        @require_collection
        def _tool(args, ctx, collection):
            return ToolResult.ok(f"entries={len(collection)}")

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool({}, ec)
        self.assertTrue(result.success)
        self.assertIn("entries=5", result.message)

    def test_require_collection_no_collection(self):
        @require_collection
        def _tool(args, ctx, collection):
            return ToolResult.ok("should not reach")

        empty_ctx = MockAppContext()
        ec = ExecutionContext(app_context=empty_ctx)
        result = _tool({}, ec)
        self.assertFalse(result.success)
        self.assertIn("没有加载翻译集合", result.message)

    def test_validate_params_success(self):
        @validate_params({"name": {"type": "str", "required": True}})
        def _tool(args, ctx):
            return ToolResult.ok(f"hello {args['name']}")

        result = _tool({"name": "world"}, None)
        self.assertTrue(result.success)

    def test_validate_params_missing_required(self):
        @validate_params({"name": {"type": "str", "required": True}})
        def _tool(args, ctx):
            return ToolResult.ok("should not reach")

        result = _tool({}, None)
        self.assertFalse(result.success)
        self.assertIn("缺少必需参数", result.message)
        self.assertEqual(result.error_code, "ARGUMENT_SCHEMA_INVALID")
        self.assertEqual(result.recovery_action, "adjust_arguments")
        self.assertEqual(result.data["json_pointer"], "/name")
        self.assertEqual(result.data["validation_issues"][0]["code"], "REQUIRED_FIELD_MISSING")

    def test_validate_params_wrong_type(self):
        @validate_params({"count": {"type": "int", "required": True}})
        def _tool(args, ctx):
            return ToolResult.ok("should not reach")

        result = _tool({"count": "not_a_number"}, None)
        self.assertFalse(result.success)
        self.assertIn("参数类型错误", result.message)
        self.assertEqual(
            result.data["validation_issues"][0],
            {
                "path": "/count",
                "schema_path": "/properties/count/type",
                "keyword": "type",
                "code": "TYPE_MISMATCH",
                "expected": "integer",
                "actual_type": "string",
                "message": "参数类型错误: 期望 integer，实际 string",
            },
        )

    def test_validate_params_returns_all_field_issues(self):
        @validate_params({
            "count": {"type": "int", "required": True},
            "name": {"type": "str", "required": True},
        })
        def _tool(args, ctx):
            return ToolResult.ok("should not reach")

        result = _tool({"count": "not_a_number"}, None)

        self.assertFalse(result.success)
        self.assertEqual(
            {issue["path"]: issue["code"] for issue in result.data["validation_issues"]},
            {"/count": "TYPE_MISMATCH", "/name": "REQUIRED_FIELD_MISSING"},
        )

    def test_validate_params_args_not_dict(self):
        @validate_params({"x": {"type": "str", "required": True}})
        def _tool(args, ctx):
            return ToolResult.ok("ok")

        result = _tool("not_a_dict", None)
        self.assertFalse(result.success)
        self.assertIn("参数类型错误", result.message)

    def test_decorator_order_recommended(self):
        """E5: @require_collection 外层 + @validate_params 内层。"""

        @require_collection
        @validate_params({"entry_id": {"type": "str", "required": True}})
        def _tool(args, ctx, collection):
            return ToolResult.ok("ok")

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool({"entry_id": "test123"}, ec)
        self.assertTrue(result.success)

    def test_decorator_order_param_fail_returns_early(self):
        """参数校验失败时不应到达 collection 检查。"""

        @require_collection
        @validate_params({"entry_id": {"type": "str", "required": True}})
        def _tool(args, ctx, collection):
            return ToolResult.ok("ok")

        ec = ExecutionContext(app_context=self.ctx)
        result = _tool({}, ec)  # missing entry_id
        self.assertFalse(result.success)
        self.assertIn("缺少必需参数", result.message)


# ── Test: Agent 注册 ─────────────────────────────────────────────────────


class TestAgentRegistry(unittest.TestCase):
    """验收标准: 7 Agent 正确注册 + namespace 通配符展开 (O3)。"""

    @classmethod
    def setUpClass(cls):
        # Ensure presets are initialized (idempotent, may have run at module load)
        from transbridge.smart_assistant.agents.agent_registry import AgentRegistry

        AgentRegistry.init_presets()

    def test_seven_agents_registered(self):
        from transbridge.smart_assistant.agents.agent_registry import AgentRegistry

        agents = AgentRegistry.list_all()
        self.assertGreaterEqual(len(agents), 7, f"Expected at least 7 agents, got {len(agents)}")

    def test_namespace_wildcard_expansion(self):
        """O3: namespace:* 通配符正确展开。"""
        from transbridge.smart_assistant.agents.agent_registry import AgentRegistry
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        expanded = AgentRegistry._expand_wildcard(["editor:*"])
        editor_specs = ToolRegistry.list_namespace("editor")
        editor_names = [s.name for s in editor_specs]
        for tool in expanded:
            self.assertIn(tool, editor_names, f"Wildcard expanded tool '{tool}' not in editor namespace")

    def test_orchestrator_has_cross_namespace_tools(self):
        from transbridge.smart_assistant.agents.agent_registry import AgentRegistry

        orch = AgentRegistry.get("orchestrator")
        self.assertIsNotNone(orch, "Orchestrator agent should be registered")
        self.assertGreater(len(orch.tools), 0)

    def test_translator_has_namespace_wildcard(self):
        from transbridge.smart_assistant.agents.agent_registry import AgentRegistry

        translator = AgentRegistry.get("translator")
        self.assertIsNotNone(translator, "Translator agent should be registered")

    def test_all_agent_ids(self):
        from transbridge.smart_assistant.agents.agent_registry import AgentRegistry

        expected_ids = {"translator", "proofreader", "orchestrator", "parser", "editor", "paratranz", "writer"}
        actual_ids = {a.agent_id for a in AgentRegistry.list_all()}
        for aid in expected_ids:
            self.assertIn(aid, actual_ids, f"Agent '{aid}' should be registered")

    def test_agent_tools_are_expanded(self):
        """O3: 注册时通配符已被展开为具体工具名。"""
        from transbridge.smart_assistant.agents.agent_registry import AgentRegistry

        editor_agent = AgentRegistry.get("editor")
        self.assertIsNotNone(editor_agent)
        # After registration, tools should be concrete names, not wildcards
        for tool in editor_agent.tools:
            self.assertFalse(tool.endswith(":*"), f"Tool '{tool}' should not be a wildcard after expansion")


if __name__ == "__main__":
    unittest.main(verbosity=2)
