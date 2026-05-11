"""工具注册表：ToolSpec + ToolRegistry + v1 工具注册。"""
from dataclasses import dataclass
from typing import Callable, Any


@dataclass
class ToolSpec:
    name: str
    display_name: str
    description: str
    parameters: dict
    is_long_running: bool = False
    execute: Callable[[dict, Any], Any] | None = None
    permission: str = "read"
    require_confirmation: bool = False
    max_output_size: int = 102400


class _ToolRegistry:
    """工具注册表（类级别单例）。支持 namespace 隔离。"""

    _namespaced_tools: dict[str, dict[str, ToolSpec]] = {"default": {}}

    @classmethod
    def register(cls, spec: ToolSpec, namespace: str = "default") -> None:
        if namespace not in cls._namespaced_tools:
            cls._namespaced_tools[namespace] = {}
        cls._namespaced_tools[namespace][spec.name] = spec

    @classmethod
    def get(cls, name: str, namespace: str | None = None) -> ToolSpec | None:
        if namespace is not None:
            return cls._namespaced_tools.get(namespace, {}).get(name)
        for ns_tools in cls._namespaced_tools.values():
            if name in ns_tools:
                return ns_tools[name]
        return None

    @classmethod
    def list_all(cls) -> list[ToolSpec]:
        seen: set[str] = set()
        result = []
        for ns_tools in cls._namespaced_tools.values():
            for name, spec in ns_tools.items():
                if name not in seen:
                    seen.add(name)
                    result.append(spec)
        return result

    @classmethod
    def list_namespace(cls, namespace: str) -> list[ToolSpec]:
        return list(cls._namespaced_tools.get(namespace, {}).values())

    @classmethod
    def list_all_namespaces(cls) -> dict[str, list[ToolSpec]]:
        return {ns: list(tools.values()) for ns, tools in cls._namespaced_tools.items()}

    @classmethod
    def build_tool_schema_for_prompt(cls, namespace: str | None = None) -> str:
        if namespace is not None:
            tools = cls._namespaced_tools.get(namespace, {})
        else:
            tools = {}
            for ns_tools in cls._namespaced_tools.values():
                tools.update(ns_tools)
        lines = ["可用工具列表："]
        for tool in tools.values():
            lines.append(f"- {tool.name}: {tool.description}")
            lines.append(f"  参数: {tool.parameters}")
        return "\n".join(lines)


ToolRegistry = _ToolRegistry


# ── v1 工具执行函数 ──────────────────────────────────────────
# 从 tools.tool_v1 导入（Story 01 迁移）

from .tools.tool_v1 import (
    _tool_lookup_terms,
    _tool_translate_entries,
    _tool_check_quality,
    _tool_get_collection_summary,
    _tool_export_json,
    _tool_write_back,
)


# ── 启动时自动注册 v1 工具 ──────────────────────────────────

def _register_v1_tools():
    ToolRegistry.register(ToolSpec(
        name="lookup_terms",
        display_name="查询术语",
        description="查询术语库中匹配的术语翻译，用于在翻译前获取标准译名",
        parameters={"keywords": {"type": "list", "description": "要查询的关键词列表"}},
        execute=_tool_lookup_terms,
        permission="read",
    ), namespace="translator")
    ToolRegistry.register(ToolSpec(
        name="translate_entries",
        display_name="翻译词条",
        description="使用 AI 翻译指定或当前选中的词条",
        parameters={"filter": {"type": "dict", "description": "可选，筛选条件"}},
        is_long_running=True,
        execute=_tool_translate_entries,
        permission="write",
    ), namespace="translator")
    ToolRegistry.register(ToolSpec(
        name="check_quality",
        display_name="质量检查",
        description="对当前集合执行翻译质量检查，返回问题列表",
        parameters={},
        execute=_tool_check_quality,
        permission="read",
    ), namespace="proofreader")
    ToolRegistry.register(ToolSpec(
        name="get_collection_summary",
        display_name="集合概况 [已废弃]",
        description="[已废弃] 请使用 get_statistics。返回当前翻译集合的统计摘要（总数、已翻译数等）",
        parameters={},
        execute=_tool_get_collection_summary,
        permission="read",
    ), namespace="default")
    ToolRegistry.register(ToolSpec(
        name="export_json",
        display_name="导出JSON",
        description="导出当前集合到 JSON 文件",
        parameters={},
        execute=_tool_export_json,
        permission="write",
    ), namespace="default")
    ToolRegistry.register(ToolSpec(
        name="write_back",
        display_name="写回译文",
        description="将译文写回到 ESP/EET/XT 文件",
        parameters={},
        is_long_running=True,
        execute=_tool_write_back,
        permission="admin",
    ), namespace="default")


_register_v1_tools()
