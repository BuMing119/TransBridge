"""Story 07: ConversationManager 测试 — 多轮对话裁剪（含 observation 消息）。"""
from __future__ import annotations

import unittest

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

    # ── 清空 ──────────────────────────────────────────────────

    def test_clear(self):
        self.cm.add_user("你好")
        self.cm.add_assistant("你好")
        self.cm.clear()
        msgs = self.cm.get_messages()
        self.assertEqual(len(msgs), 0)

if __name__ == "__main__":
    unittest.main()
