"""Story 07: MCP 模块测试 — auth / tools/list / tools/call / 错误处理。"""
from __future__ import annotations

import json
import unittest


class FakeToolRegistry:
    def get(self, name, namespace=None):
        from src.transbridge.smart_assistant.tool_registry import ToolSpec
        return ToolSpec(name=name, display_name=name, description="fake", parameters={})


class TestMCPAuth(unittest.TestCase):
    """MCP Server 认证逻辑测试。"""

    def setUp(self):
        from src.transbridge.smart_assistant.mcp.server import MCPServer
        self.server = MCPServer(FakeToolRegistry())

    def _make_request(self, auth=""):
        return {
            "jsonrpc": "2.0", "id": 1, "method": "tools/list",
            "params": {"_meta": {"authorization": auth}},
        }

    def test_no_auth_when_token_empty(self):
        """空 auth_token 时认证应通过。"""
        self.server._config["auth_token"] = ""
        self.assertTrue(self.server._authenticate(self._make_request()))

    def test_no_auth_when_token_whitespace(self):
        self.server._config["auth_token"] = "  "
        self.assertTrue(self.server._authenticate(self._make_request()))

    def test_auth_rejected_wrong_token(self):
        self.server._config["auth_token"] = "secret123"
        self.assertFalse(self.server._authenticate(self._make_request("wrong_token")))

    def test_auth_accepted_correct_token(self):
        self.server._config["auth_token"] = "secret123"
        self.assertTrue(self.server._authenticate(self._make_request("secret123")))


class TestMCPToolHandling(unittest.TestCase):
    """MCP 工具列表/调用逻辑测试。"""

    def setUp(self):
        # 确保工具已注册
        import src.transbridge.smart_assistant.tools.tool_default
        import src.transbridge.smart_assistant.tools.tool_translator
        from src.transbridge.smart_assistant.tool_registry import ToolRegistry
        self.registry = ToolRegistry

    def test_tools_list_not_empty(self):
        tools = self.registry.list_all()
        self.assertGreater(len(tools), 10, "应至少有 10 个已注册工具")

    def test_deprecated_tools_in_registry_but_not_prompt(self):
        """M2: deprecated 工具仍在 registry 中（兼容旧调用），但不出现在 prompt schema 中。"""
        schema = self.registry.build_tool_schema_for_prompt()
        self.assertNotIn("lookup_terms", schema, "deprecated v1 工具不应出现在 prompt")
        self.assertNotIn("translate_entries", schema)
        self.assertNotIn("write_back", schema)
        self.assertNotIn("check_quality", schema)
        self.assertNotIn("export_json", schema)

    def test_non_deprecated_tools_in_prompt(self):
        schema = self.registry.build_tool_schema_for_prompt()
        self.assertIn("start_translation", schema)
        self.assertIn("get_app_state", schema)

    def test_tool_get_by_namespace(self):
        """按 namespace 查找工具可找到非 deprecated 工具。"""
        spec = self.registry.get("start_translation", namespace="translator")
        self.assertIsNotNone(spec)

    def test_tool_get_nonexistent(self):
        spec = self.registry.get("nonexistent_tool_xyz")
        self.assertIsNone(spec)

    def test_deprecated_not_in_prompt(self):
        """确保所有 5 个 deprecated v1 工具都不在 prompt 中。"""
        schema = self.registry.build_tool_schema_for_prompt()
        deprecated_names = ["lookup_terms", "translate_entries", "check_quality", "export_json", "write_back"]
        for name in deprecated_names:
            self.assertNotIn(name, schema, f"deprecated 工具 {name} 不应出现在 prompt schema")


if __name__ == "__main__":
    unittest.main()
