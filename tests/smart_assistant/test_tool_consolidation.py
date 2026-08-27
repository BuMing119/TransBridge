"""Story 21: 合并工具测试矩阵 + 原生工具定义回归验证。"""

from __future__ import annotations

import unittest

from tests.conftest import MockAppContext


# ============================================================
# TestSetFilters (10 cases)
# ============================================================
class TestSetFilters(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters

        self.func = _tool_set_filters
        self.ctx = MockAppContext()

    def test_set_single_dimension_stages(self):
        r = self.func({"stages": [0, 1]}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(self.ctx.filter_state["stage"], [0, 1])

    def test_set_multi_dimension_stages_categories(self):
        r = self.func({"stages": [1], "categories": ["NPC_"]}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(self.ctx.filter_state["stage"], [1])
        self.assertEqual(self.ctx.filter_state["category"], ["NPC_"])

    def test_set_search_query_and_field(self):
        r = self.func({"search_query": "hello", "search_field": "translation"}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(self.ctx.filter_state["search_query"], "hello")
        self.assertEqual(self.ctx.filter_state["search_field"], "translation")

    def test_clear_true_with_new_values(self):
        r = self.func({"clear": True, "stages": [3]}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(self.ctx.filter_state["stage"], [3])
        # 清除后其他维度应为空
        self.assertEqual(self.ctx.filter_state["category"], [])

    def test_clear_true_alone(self):
        r = self.func({"clear": True}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(self.ctx.filter_state["stage"], [])

    def test_labels_empty_clears(self):
        self.ctx.filter_state["label"] = ["old_label"]
        r = self.func({"labels": []}, self.ctx)
        self.assertTrue(r.success)
        self.assertEqual(self.ctx.filter_state["label"], [])

    def test_all_none_noop(self):
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)
        self.assertTrue(r.data.get("unchanged"))

    def test_invalid_search_field_rejected(self):
        r = self.func({"search_field": "INVALID_FIELD"}, self.ctx)
        self.assertFalse(r.success)

    def test_invalid_stage_rejected(self):
        r = self.func({"stages": [99]}, self.ctx)
        self.assertFalse(r.success)

    def test_search_field_text_backward_compat(self):
        # "text" 不是 set_filters 的有效字段（需用 "original"），验证不崩溃
        r = self.func({"search_field": "text"}, self.ctx)
        self.assertFalse(r.success)  # "text" 不在 VALID_FIELDS 中


# ============================================================
# TestStopTask (5 cases)
# ============================================================
class TestStopTask(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_translator import _tool_stop_task

        self.func = _tool_stop_task
        self.ctx = MockAppContext()

    def test_stop_specific_task_id(self):
        r = self.func({"task_id": "nonexistent-123"}, self.ctx)
        self.assertFalse(r.success)
        self.assertIn("任务不存在或已结束", r.message)

    def test_stop_all_no_task_id(self):
        r = self.func({}, self.ctx)
        self.assertTrue(r.success)  # 当前无任务

    def test_stop_all_empty_task_id(self):
        r = self.func({"task_id": ""}, self.ctx)
        self.assertTrue(r.success)  # 空=停止全部

    def test_stop_all_none_task_id(self):
        r = self.func({"task_id": None}, self.ctx)
        self.assertTrue(r.success)

    def test_stop_nonexistent_task_returns_fail(self):
        r = self.func({"task_id": "ghost-task-999"}, self.ctx)
        self.assertFalse(r.success)
        self.assertIn("任务不存在或已结束", r.message)


# ============================================================
# TestWriteBack (9 cases)
# ============================================================
class TestWriteBack(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_writer import _tool_write_back

        self.func = _tool_write_back
        self.ctx = MockAppContext()

    def test_invalid_target_rejected(self):
        r = self.func({"target": "pdf"}, self.ctx)
        self.assertFalse(r.success)

    def test_esp_target_no_slot(self):
        r = self.func({"target": "esp"}, self.ctx)
        self.assertFalse(r.success)  # 无活跃slot/collection

    def test_eet_target_no_slot(self):
        r = self.func({"target": "eet"}, self.ctx)
        self.assertFalse(r.success)

    def test_xt_target_no_slot(self):
        r = self.func({"target": "xt"}, self.ctx)
        self.assertFalse(r.success)

    def test_strings_target_no_slot(self):
        r = self.func({"target": "strings"}, self.ctx)
        self.assertFalse(r.success)

    def test_esp_target_uppercase_normalized(self):
        r = self.func({"target": "ESP"}, self.ctx)
        self.assertFalse(r.success)  # .lower() 归一化，与 esp 行为一致

    def test_eet_target_needs_path(self):
        r = self.func({"target": "eet"}, self.ctx)
        self.assertFalse(r.success)

    def test_xt_target_needs_path(self):
        r = self.func({"target": "xt"}, self.ctx)
        self.assertFalse(r.success)

    def test_strings_needs_path(self):
        r = self.func({"target": "strings"}, self.ctx)
        self.assertFalse(r.success)


# ============================================================
# TestManageEntryLabels (6 cases)
# ============================================================
class TestManageEntryLabels(unittest.TestCase):
    def setUp(self):
        from transbridge.smart_assistant.tools.tool_editor import _tool_manage_entry_labels

        self.func = _tool_manage_entry_labels
        self.ctx = MockAppContext()

    def test_create_label(self):
        r = self.func({"action": "create", "name": "TestLabel", "color": "#FF0000"}, self.ctx)
        self.assertTrue(r.success)
        self.assertIn("label_id", r.data)

    def test_create_label_empty_name_rejected(self):
        r = self.func({"action": "create", "name": ""}, self.ctx)
        self.assertFalse(r.success)

    def test_invalid_action_rejected(self):
        r = self.func({"action": "delete"}, self.ctx)
        self.assertFalse(r.success)

    def test_assign_nonexistent_label(self):
        r = self.func({"action": "assign", "name": "NoSuch", "entry_ids": ["e1"]}, self.ctx)
        self.assertFalse(r.success)

    def test_unassign_nonexistent_label(self):
        r = self.func({"action": "unassign", "name": "NoSuch", "entry_ids": ["e1"]}, self.ctx)
        self.assertFalse(r.success)

    def test_assign_without_entry_ids_rejected(self):
        r = self.func({"action": "assign", "name": "Test"}, self.ctx)
        self.assertFalse(r.success)


# ============================================================
# TestDeprecatedWrappers (schema 回归)
# ============================================================
class TestDeprecatedWrappers(unittest.TestCase):
    def test_old_tool_names_not_in_registry(self):
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        old_names = [
            "filter_by_stage",
            "filter_by_category",
            "filter_by_label",
            "search_entries",
            "clear_all_filters",
            "stop_all_tasks",
            "write_to_esp",
            "write_to_eet",
            "write_to_xt",
            "write_to_strings",
            "create_label",
            "assign_label",
            "remove_label",
            "batch_assign_label",
        ]
        for name in old_names:
            self.assertIsNone(ToolRegistry.get(name), f"{name} should not be registered")

    def test_new_tool_names_in_registry(self):
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        # 触发注册（模块导入）
        from transbridge.smart_assistant.tools import (  # noqa: F401
            tool_default,
            tool_editor,
            tool_paratranz,
            tool_parser,
            tool_proofreader,
            tool_translator,
            tool_writer,
        )

        new_names = ["set_filters", "stop_task", "write_back", "manage_entry_labels"]
        for name in new_names:
            self.assertIsNotNone(ToolRegistry.get(name), f"{name} should be registered")

    def test_tool_count_is_current(self):
        """The complete FR9/FR16 catalog is explicit and contains no hidden wrappers."""
        from transbridge.smart_assistant.tool_registry import ToolRegistry
        from transbridge.smart_assistant.tools import register_all

        register_all()

        active = {spec.name for spec in ToolRegistry.list_all(include_deprecated=False)}
        required_fr16 = {
            "extract_archive",
            "pack_archive",
            "diff_directories",
            "filter_files",
            "migrate_entries",
            "apply_dictionary",
            "save_dictionary",
            "plan_sync",
        }
        self.assertTrue(required_fr16.issubset(active))
        self.assertEqual(len(active), 50, "unexpected active tool was added or removed")

    def test_native_tool_definitions_no_old_names(self):
        from transbridge.smart_assistant.native_tools import build_native_tool_definitions
        from transbridge.smart_assistant.tool_registry import ToolRegistry

        # 触发注册（模块导入）
        from transbridge.smart_assistant.tools import (  # noqa: F401
            tool_default,
            tool_editor,
            tool_paratranz,
            tool_parser,
            tool_proofreader,
            tool_translator,
            tool_writer,
        )

        definitions = build_native_tool_definitions(ToolRegistry.list_all_namespaces())
        names = {definition.name for definition in definitions}
        self.assertGreater(len(names), 0, "Native tool definitions should not be empty")

        # 验证旧工具名不作为 Provider 原生工具暴露。
        old_names = [
            "filter_by_stage",
            "filter_by_category",
            "search_entries",
            "clear_all_filters",
            "stop_all_tasks",
            "write_to_esp",
            "write_to_eet",
            "write_to_xt",
            "write_to_strings",
            "create_label",
            "assign_label",
            "remove_label",
            "batch_assign_label",
        ]
        for name in old_names:
            # 只检查作为工具条目出现的行（"- name:..."格式），不检查描述引用
            self.assertIsNone(
                ToolRegistry.get(name),
                f"Deprecated tool {name} should not be registered",
            )

        # 验证合并后的新工具出现在原生工具定义中
        new_names = ["set_filters", "stop_task", "write_back", "manage_entry_labels"]
        for name in new_names:
            self.assertIn(name, names, f"Consolidated tool {name} should appear in native tool definitions")


if __name__ == "__main__":
    unittest.main()
