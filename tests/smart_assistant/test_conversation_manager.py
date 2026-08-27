"""Story 07: ConversationManager 测试 — 多轮对话裁剪（含 observation 消息）。"""

from __future__ import annotations

import unittest

from transbridge.infra.llm_tool_calling import LlmToolCall, LlmToolProtocolError, LlmTurn
from transbridge.smart_assistant.conversation_manager import ConversationManager


def _make_msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


class TestConversationManager(unittest.TestCase):
    """对话管理核心逻辑测试。"""

    def setUp(self):
        self.cm = ConversationManager(max_turns=5)

    # ── 基本操作 ──────────────────────────────────────────────

    def test_add_user_assistant_pair(self):
        self.cm.add_user("你好")
        self.cm.add_assistant("你好，有什么可以帮你的？")
        msgs = self.cm.get_messages()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertEqual(msgs[1]["role"], "assistant")

    def test_add_system_message(self):
        self.cm.add_system("系统初始化完成")
        msgs = self.cm.get_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["role"], "system")

    # ── 裁剪逻辑 ──────────────────────────────────────────────

    def test_no_trim_within_max_turns(self):
        for i in range(3):
            self.cm.add_user(f"消息{i}")
            self.cm.add_assistant(f"回复{i}")
        msgs = self.cm.get_messages()
        self.assertEqual(len(msgs), 6)

    def test_trim_exceeds_max_turns(self):
        for i in range(8):
            self.cm.add_user(f"消息{i}")
            self.cm.add_assistant(f"回复{i}")
        msgs = self.cm.get_messages()
        # 应该保留最后 5 轮 = 10 条 user+assistant 消息
        self.assertLessEqual(len(msgs), 12)

    def test_trim_preserves_last_turns(self):
        for i in range(10):
            self.cm.add_user(f"消息{i}")
            self.cm.add_assistant(f"回复{i}")
        msgs = self.cm.get_messages()
        # 第 10 轮应该是"消息9"/"回复9"
        last_user = next(m for m in reversed(msgs) if m["role"] == "assistant")
        self.assertIn("回复9", last_user["content"])

    # ── observation 消息 ──────────────────────────────────────

    def test_add_observation(self):
        self.cm.add_user("翻译 DLC1")
        self.cm.add_assistant("启动翻译...")
        self.cm.add_observation("start_translation", "翻译任务 task_001 完成: 成功 50, 失败 2")
        msgs = self.cm.get_messages()
        roles = [m["role"] for m in msgs]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertIn("user", roles[2:])  # observation 以 user 角色注入

    def test_trim_includes_observations(self):
        for i in range(8):
            self.cm.add_user(f"msg{i}")
            self.cm.add_assistant(f"reply{i}")
            self.cm.add_observation("tool", f"result{i}")
        msgs = self.cm.get_messages()
        # max_turns=5, 8 rounds should be trimmed
        self.assertLess(len(msgs), 24)  # < 8*3

    def test_add_plan_result(self):
        self.cm.add_user("执行计划")
        self.cm.add_assistant("计划已确认")
        self.cm.add_plan_result("步骤1完成\n步骤2完成")
        msgs = self.cm.get_messages()
        self.assertGreater(len(msgs), 2)

    def test_native_plan_result_closes_propose_plan_call(self):
        self.cm.add_user("执行计划")
        self.cm.add_assistant_turn(
            LlmTurn(
                tool_calls=(
                    LlmToolCall(
                        "plan-1",
                        "propose_plan",
                        {"summary": "plan", "steps": []},
                    ),
                )
            )
        )
        self.cm.add_plan_result("步骤完成")

        result = self.cm.get_messages()[-1]
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "plan-1")
        self.assertIn("步骤完成", result["display_summary"])

    def test_native_tool_call_and_result_are_serializable(self):
        self.cm.add_user("查看统计")
        self.cm.add_assistant_turn(
            LlmTurn(tool_calls=(LlmToolCall("call-1", "get_statistics", {}),), stop_reason="tool_calls")
        )
        self.cm.add_tool_result(
            "call-1",
            "get_statistics",
            {"success": True, "total": 12},
            display_summary="[OK] 共 12 条",
        )

        data = self.cm.to_dict()
        restored = ConversationManager()
        restored.from_dict(data)

        messages = restored.get_messages()
        self.assertEqual(messages[1]["tool_calls"][0]["id"], "call-1")
        self.assertEqual(messages[2]["role"], "tool")
        self.assertEqual(messages[2]["tool_call_id"], "call-1")

    def test_restore_closes_unresolved_native_tool_call(self):
        self.cm.from_dict({
            "messages": [
                {"role": "user", "content": "run"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "call-pending", "name": "get_statistics", "arguments": {}}],
                },
            ]
        })

        result = self.cm.get_messages()[-1]
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "call-pending")
        self.assertTrue(result["is_error"])

    def test_tool_result_does_not_count_as_user_turn(self):
        self.cm.from_dict({
            "messages": [
                {"role": "user", "content": "run"},
                {"role": "tool", "tool_call_id": "call-1", "name": "x", "content": "{}"},
            ]
        })
        self.assertEqual(self.cm._turn_starts, [0])

    def test_legacy_observation_does_not_count_as_user_turn_after_restore(self):
        self.cm.from_dict({
            "messages": [
                {"role": "user", "content": "run"},
                {"role": "assistant", "content": "calling"},
                {"role": "user", "content": "【工具执行结果 - x】\nok"},
            ]
        })
        self.assertEqual(self.cm._turn_starts, [0])

    def test_duplicate_tool_result_is_idempotent(self):
        self.cm.add_tool_result("call-1", "x", {"success": True})
        self.cm.add_tool_result("call-1", "x", {"success": False}, is_error=True)

        results = [message for message in self.cm.get_messages() if message.get("role") == "tool"]
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["is_error"])

    def test_reused_tool_call_id_across_turns_is_rejected_before_persisting(self):
        self.cm.add_assistant_turn(LlmTurn(tool_calls=(LlmToolCall("call-1", "get_statistics", {}),)))
        self.cm.add_tool_result("call-1", "get_statistics", {"success": True})

        with self.assertRaisesRegex(LlmToolProtocolError, "historical tool call ids"):
            self.cm.add_assistant_turn(LlmTurn(tool_calls=(LlmToolCall("call-1", "get_app_state", {}),)))

        assistant_calls = [message for message in self.cm.get_messages() if message.get("tool_calls")]
        self.assertEqual(len(assistant_calls), 1)

    def test_loaded_namespaces_round_trip(self):
        self.cm.load_tool_namespaces(["translator", "parser"])
        restored = ConversationManager()
        restored.from_dict(self.cm.to_dict())
        self.assertEqual(restored.get_loaded_tool_namespaces(), ("parser", "translator"))

    def test_cancelled_tool_help_does_not_restore_namespace(self):
        self.cm.from_dict({
            "messages": [
                {"role": "user", "content": "load parser"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "help-1",
                            "name": "get_tool_help",
                            "arguments": {"namespace": "parser"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "help-1",
                    "name": "get_tool_help",
                    "content": "{}",
                    "is_error": True,
                },
            ]
        })
        self.assertEqual(self.cm.get_loaded_tool_namespaces(), ())

    # ── 清空 ──────────────────────────────────────────────────

    def test_clear(self):
        self.cm.add_user("你好")
        self.cm.add_assistant("你好")
        self.cm.clear()
        msgs = self.cm.get_messages()
        self.assertEqual(len(msgs), 0)


if __name__ == "__main__":
    unittest.main()
