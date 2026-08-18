"""工具注册表：规范 ToolSpec、启动校验与 namespace 查询。"""
from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any

from transbridge.application.tools.schema import (
    LegacySchemaConversionError,
    canonicalize_parameters,
)


class DuplicateToolError(RuntimeError):
    """A duplicate tool name makes startup ambiguous."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    display_name: str
    description: str
    parameters: dict
    summary: str = ""          # 一句话摘要（~30-50 chars），从 description ① 段自动提取
    is_long_running: bool = False
    execute: Callable[[dict, Any], Any] | None = None
    permission: str = "read"
    require_confirmation: bool = False
    max_output_size: int = 102400
    deprecated: bool = False  # M2: 标记已废弃工具
    available: bool = True
    unavailable_reason: str = ""

    def __post_init__(self):
        if not self.summary and self.description:
            m = re.match(r'①(.+?)(?:②|$)', self.description)
            if m:
                object.__setattr__(self, "summary", m.group(1).strip()[:50])
        try:
            canonical = canonicalize_parameters(self.parameters)
        except LegacySchemaConversionError as exc:
            object.__setattr__(self, "available", False)
            object.__setattr__(self, "unavailable_reason", str(exc))
            canonical = canonicalize_parameters({})
        object.__setattr__(self, "parameters", canonical)


class _ToolRegistry:
    """工具注册表（类级别单例）。支持 namespace 隔离。

    NOTE: 类名以下划线开头表示模块内部实现细节，但通过 ToolRegistry = _ToolRegistry
    别名对外暴露为公共接口。保留 _ 前缀以维持历史向后兼容。
    """

    _namespaced_tools: dict[str, dict[str, ToolSpec]] = {"default": {}}

    @classmethod
    def register(cls, spec: ToolSpec, namespace: str = "default") -> None:
        """注册工具 spec 到指定 namespace。"""
        existing = cls.get(spec.name)
        if existing is not None:
            raise DuplicateToolError(f"重复工具名: {spec.name}")
        if namespace not in cls._namespaced_tools:
            cls._namespaced_tools[namespace] = {}
        cls._namespaced_tools[namespace][spec.name] = spec

    @classmethod
    def get(cls, name: str, namespace: str | None = None) -> ToolSpec | None:
        """按名称查找工具。不指定 namespace 时遍历所有 namespace。"""
        if namespace is not None:
            return cls._namespaced_tools.get(namespace, {}).get(name)
        for ns_tools in cls._namespaced_tools.values():
            if name in ns_tools:
                return ns_tools[name]
        return None

    @classmethod
    def list_all(cls, include_deprecated: bool = False) -> list[ToolSpec]:
        """列出所有 namespace 中的工具（去重）。M2: include_deprecated 默认 False，排除已废弃工具。"""
        seen: set[str] = set()
        result = []
        for ns_tools in cls._namespaced_tools.values():
            for name, spec in ns_tools.items():
                if name not in seen:
                    seen.add(name)
                    if include_deprecated or not spec.deprecated:
                        result.append(spec)
        return result

    @classmethod
    def list_namespace(cls, namespace: str) -> list[ToolSpec]:
        """列出指定 namespace 中的所有工具。"""
        return list(cls._namespaced_tools.get(namespace, {}).values())

    @classmethod
    def list_all_namespaces(cls) -> dict[str, list[ToolSpec]]:
        """返回所有 namespace 及其工具列表。"""
        return {ns: list(tools.values()) for ns, tools in cls._namespaced_tools.items()}

    @classmethod
    def build_tool_schema_for_prompt(cls, namespace: str | None = None) -> str:
        """构建工具 schema 文本供 LLM prompt 注入。M2: 排除 deprecated 工具。"""
        if namespace is not None:
            tools = cls._namespaced_tools.get(namespace, {})
        else:
            tools = {}
            for ns_tools in cls._namespaced_tools.values():
                tools.update(ns_tools)
        lines = ["可用工具列表："]
        for tool in tools.values():
            if tool.deprecated or not tool.available:
                continue
            lines.append(f"- {tool.name}: {tool.description}")
            lines.append(f"  参数: {tool.parameters}")
        return "\n".join(lines)

    @classmethod
    def register_tools(cls, namespace: str, tool_defs: list[dict]) -> None:
        """C21: 批量注册工具，封装各模块重复的"定义元组→遍历→注册"样板。

        tool_def 字段: name, display_name, description, execute,
          parameters={}, permission="read", is_long_running=False,
          require_confirmation=False, deprecated=False, max_output_size=102400
        """
        for td in tool_defs:
            cls.register(ToolSpec(
                name=td["name"],
                display_name=td["display_name"],
                description=td["description"],
                parameters=td.get("parameters", {}),
                execute=td.get("execute"),
                permission=td.get("permission", "read"),
                is_long_running=td.get("is_long_running", False),
                require_confirmation=td.get("require_confirmation", False),
                deprecated=td.get("deprecated", False),
                max_output_size=td.get("max_output_size", 102400),
            ), namespace=namespace)

    @classmethod
    def build_tool_directory(cls) -> str:
        """构建精简工具目录（namespace 标签 + name + 一句话摘要）。~500 tokens。"""
        lines = ["## 可用工具目录"]
        all_ns = cls.list_all_namespaces()
        ns_order = ["default"] + sorted(ns for ns in all_ns if ns != "default")
        for ns in ns_order:
            tools = all_ns.get(ns, [])
            if not tools:
                continue
            for spec in sorted(tools, key=lambda s: s.name):
                if spec.deprecated or not spec.available:
                    continue
                summary = spec.summary or spec.description[:50]
                lines.append(f"[{ns}] {spec.name} — {summary}")
        return "\n".join(lines)

    @classmethod
    def build_tool_help(cls, tool: str | None = None,
                        namespace: str | None = None) -> str:
        """返回指定工具或 namespace 的完整 Schema（结构化参数表格）。

        三种模式：tool → 单工具；namespace → 整组（支持逗号分隔）；皆空 → 全局概览。
        """
        if tool is not None:
            return cls._help_single_tool(tool)
        elif namespace is not None:
            return cls._help_namespaces(namespace)
        else:
            return cls._help_overview()

    @classmethod
    def _help_single_tool(cls, name: str) -> str:
        spec = cls.get(name)
        if spec is None:
            all_names = [s.name for s in cls.list_all(include_deprecated=True)]
            matches = [n for n in all_names
                       if _levenshtein_distance(name.lower(), n.lower()) <= 3]
            if matches:
                return f"未找到 '{name}'，您是否要找: {', '.join(matches)}？"
            return f"未找到工具 '{name}'。使用 get_tool_help() 查看可用工具列表。"
        if not spec.available:
            return f"工具 '{name}' 当前不可用: {spec.unavailable_reason}"
        return cls._format_tool_schema(spec)

    @classmethod
    def _help_namespaces(cls, ns_str: str) -> str:
        parts = []
        for ns in ns_str.split(","):
            ns = ns.strip()
            tools = cls.list_namespace(ns)
            if not tools:
                parts.append(f"## {ns}\n（命名空间不存在或为空）")
                continue
            parts.append(f"## {ns}")
            for spec in sorted(tools, key=lambda s: s.name):
                if spec.deprecated or not spec.available:
                    continue
                parts.append(cls._format_tool_schema(spec))
        return "\n\n".join(parts)

    @classmethod
    def _help_overview(cls) -> str:
        lines = ["## 工具概览"]
        all_ns = cls.list_all_namespaces()
        ns_order = ["default"] + sorted(ns for ns in all_ns if ns != "default")
        for ns in ns_order:
            tools = all_ns.get(ns, [])
            active = [t for t in tools if not t.deprecated and t.available]
            if not active:
                continue
            lines.append(f"\n### {ns}")
            for spec in sorted(active, key=lambda s: s.name):
                summary = spec.summary or spec.description[:50]
                lines.append(f"- **{spec.name}**: {summary}")
        return "\n".join(lines)

    @staticmethod
    def _format_tool_schema(spec: ToolSpec) -> str:
        lines = [f"### {spec.name}", f"> {spec.description}"]
        properties = spec.parameters.get("properties", {})
        required_names = set(spec.parameters.get("required", ()))
        if properties:
            lines.append("| 参数 | 类型 | 必填 | 说明 |")
            lines.append("|------|------|------|------|")
            for pname, pinfo in properties.items():
                required = "是" if pname in required_names else "否"
                ptype = pinfo.get("type", "string")
                desc = pinfo.get("description", "")
                lines.append(f"| {pname} | {ptype} | {required} | {desc} |")
        else:
            lines.append("（无参数）")
        if spec.is_long_running:
            lines.append("\n**类型**: 长时间运行（异步）")
        if spec.require_confirmation:
            lines.append("\n**注意**: 此工具需要用户确认后才能执行。")
        return "\n".join(lines)

    @classmethod
    def init_defaults(cls) -> None:
        """v1 废弃工具已移除（2026-05-20）。保留此空方法避免调用方报错。"""
        pass


def _levenshtein_distance(a: str, b: str) -> int:
    """计算两个字符串的编辑距离。"""
    if len(a) < len(b):
        return _levenshtein_distance(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


ToolRegistry = _ToolRegistry
