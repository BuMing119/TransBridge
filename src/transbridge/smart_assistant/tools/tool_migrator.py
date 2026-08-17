"""词条键对齐迁移 + 词典套用/存词典 Agent 工具。

FR16.4 migrate_entries（editor namespace）；
FR16.5 apply_dictionary / save_dictionary（translator namespace）。
"""
from __future__ import annotations

from .base import ToolResult, require_collection


_PARAM_SCHEMAS = {
    "migrate_entries": {
        "old_collection": {"type": "str", "required": False, "description": "旧集合来源（预留，缺省用当前已加载集合的前一版本）"},
    },
    "apply_dictionary": {
        "overwrite": {"type": "bool", "required": False, "description": "是否覆盖已有译文（默认 false）"},
    },
    "save_dictionary": {
        "mod_file_id": {"type": "str", "required": False, "description": "词典的 mod_file_id（默认空串=全局）"},
        "scope": {"type": "str", "required": False, "description": "project/global（默认 global）"},
    },
}


@require_collection
def _tool_apply_dictionary(args: dict, ctx, collection) -> ToolResult:
    """调用 TranslationMemoryManager.apply_to_collection() 套用词典。"""
    try:
        from src.transbridge.translation_memory.manager import TranslationMemoryManager, QueryContext
        manager = TranslationMemoryManager()
        manager.load()
        context = QueryContext()
        result = manager.apply_to_collection(
            collection, context=context, overwrite=bool(args.get("overwrite", False)),
        )
        data = {
            "applied": result.applied,
            "key_hits": result.key_hits,
            "text_hits": result.text_hits,
            "misses": result.misses,
            "needs_review": result.needs_review,
            "conflicts": len(result.conflicts),
        }
        return ToolResult.ok(
            f"词典套用: 命中{result.applied}(键{result.key_hits}/文本{result.text_hits}) 未命中{result.misses}",
            data=data,
        )
    except Exception as exc:
        return ToolResult.fail(f"词典套用失败: {exc}", error_category="internal")


@require_collection
def _tool_save_dictionary(args: dict, ctx, collection) -> ToolResult:
    """调用 TranslationMemoryManager.save_from_collection() 存词典。"""
    try:
        from src.transbridge.translation_memory.manager import TranslationMemoryManager
        from src.transbridge.translation_memory.model import SCOPE_GLOBAL
        manager = TranslationMemoryManager()
        manager.load()
        added = manager.save_from_collection(
            collection,
            mod_file_id=args.get("mod_file_id", ""),
            scope=args.get("scope", SCOPE_GLOBAL),
        )
        manager.save()
        return ToolResult.ok(f"已存词典，新增 {added} 条", data={"added": added})
    except Exception as exc:
        return ToolResult.fail(f"存词典失败: {exc}", error_category="internal")


def _tool_migrate_entries(args: dict, ctx) -> ToolResult:
    """词条键对齐：当前集合 vs 上一版本集合（预留，实际由 FOMOD 流水线驱动）。"""
    return ToolResult.fail(
        "migrate_entries 需要新旧两个集合；当前无归档上下文，请通过 FOMOD 流水线调用（FR15 后置需求）",
        error_category="config", error_code="NOT_AVAILABLE",
    )


def _register_migrator_tools():
    from ..tool_registry import ToolRegistry
    # migrate_entries 挂 editor namespace
    ToolRegistry.register_tools("editor", [
        {"name": "migrate_entries", "display_name": "词条键对齐迁移",
         "description": "①按entry.key将旧集合译文对齐到新集合同名键条目。②参数: (预留)。③返回: {inherited,needs_review,missed}。④规则: 仅键精确匹配+原文变化检测，不做文本兜底；文本兜底是词典套用(apply_dictionary)的职责。",
         "execute": _tool_migrate_entries, "permission": "write",
         "parameters": _PARAM_SCHEMAS["migrate_entries"]},
    ])
    # 词典工具挂 translator namespace
    ToolRegistry.register_tools("translator", [
        {"name": "apply_dictionary", "display_name": "词典套用",
         "description": "①将翻译记忆词典套用到当前集合填补空译文。②参数: overwrite(可选,默认false)。③返回{applied,key_hits,text_hits,misses,needs_review,conflicts}。④规则: 键索引优先+文本索引兜底。",
         "execute": _tool_apply_dictionary, "permission": "write",
         "parameters": _PARAM_SCHEMAS["apply_dictionary"]},
        {"name": "save_dictionary", "display_name": "存为词典",
         "description": "①将当前集合已译条目写入翻译记忆词典。②参数: mod_file_id(可选), scope(可选project/global)。③返回{added}。④规则: 排除锁定/隐藏/空译文条目。",
         "execute": _tool_save_dictionary, "permission": "write",
         "parameters": _PARAM_SCHEMAS["save_dictionary"]},
    ])


_register_migrator_tools()