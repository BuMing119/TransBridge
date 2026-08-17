"""Agent 工具系统子包 — 工具定义、执行、管理。

NOTE (QA-007): 注册模式不一致 — 当前 tools/skills/file_parser 三个子包使用了
三种不同的注册方式：
  - tools:     模块导入时通过副作用自动注册到 ToolRegistry (import side-effect)
  - skills:    SkillRegistry 集中管理，通过 SkillLoader 显式注册
  - file_parser: 类导入后由调用方手动扫描子类进行注册
未来清理时应统一为一种模式（推荐显式注册，消除导入副作用）。

推荐 API:
  调用 register_all() 显式注册所有工具（无导入副作用）。
  模块级的 side-effect 导入已标记为 DEPRECATED，仅保留向后兼容。
"""
from .base import ToolResult, ExecutionContext, HITLRequest, HITLResponse, HITLType
from .base import execute_with_guardrails, filter_entries
from .base import require_collection, validate_params


def register_all() -> None:
    """显式注册所有内置工具（推荐 API，无导入副作用）。

    显式导入各工具模块，触发各自的 _register_*_tools() 调用。
    应用启动时调用一次即可。
    """
    from . import (          # noqa: F401
        tool_editor,
        tool_translator,
        tool_proofreader,
        tool_paratranz,
        tool_writer,
        tool_parser,
        tool_default,
        tool_archive,
        tool_migrator,
    )


__all__ = [
    "ToolResult",
    "ExecutionContext",
    "HITLRequest",
    "HITLResponse",
    "HITLType",
    "execute_with_guardrails",
    "filter_entries",
    "require_collection",
    "validate_params",
    "register_all",
]
