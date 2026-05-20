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
    deprecated: bool = False  # M2: 标记已废弃工具


class _ToolRegistry:
    """工具注册表（类级别单例）。支持 namespace 隔离。

    NOTE: 类名以下划线开头表示模块内部实现细节，但通过 ToolRegistry = _ToolRegistry
    别名对外暴露为公共接口。保留 _ 前缀以维持历史向后兼容。
    """

    _namespaced_tools: dict[str, dict[str, ToolSpec]] = {"default": {}}

    @classmethod
    def register(cls, spec: ToolSpec, namespace: str = "default") -> None:
        """注册工具 spec 到指定 namespace。"""
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
            if tool.deprecated:
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
    def init_defaults(cls) -> None:
        """v1 废弃工具已移除（2026-05-20）。保留此空方法避免调用方报错。"""
        pass


ToolRegistry = _ToolRegistry
