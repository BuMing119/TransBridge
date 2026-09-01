"""词条键对齐迁移 + 词典套用/存词典 Agent 工具。

FR16.4 migrate_entries（editor namespace）；
FR16.5 apply_dictionary / save_dictionary（translator namespace）。
"""

from __future__ import annotations

from .base import ToolResult, require_collection

_PARAM_SCHEMAS = {
    "migrate_entries": {
        "old_collection": {
            "type": "str",
            "required": False,
            "description": "Reserved old-collection source; defaults to the previous version of the loaded collection",
        },
    },
    "apply_dictionary": {
        "overwrite": {
            "type": "bool",
            "required": False,
            "description": "Whether to overwrite existing translations; default false",
        },
    },
    "save_dictionary": {
        "mod_file_id": {"type": "str", "required": False, "description": "Dictionary mod_file_id; empty means global"},
        "scope": {"type": "str", "required": False, "description": "project or global; default global"},
    },
}

for _tool_name in ("apply_dictionary", "save_dictionary"):
    for _locale in ("source_locale", "target_locale"):
        _PARAM_SCHEMAS[_tool_name][_locale] = {
            "type": "str",
            "required": False,
            "description": "Explicit language locale; required for apply unless captured in the request context",
        }


@require_collection
def _tool_apply_dictionary(args: dict, ctx, collection) -> ToolResult:
    """Apply only candidates accepted by the current locale/source contract."""
    try:
        from ._dictionary_application import apply_dictionary

        return apply_dictionary(args, ctx, collection)
    except Exception as exc:
        return ToolResult.fail(f"词典套用失败: {exc}", error_category="internal")


@require_collection
def _tool_save_dictionary(args: dict, ctx, collection) -> ToolResult:
    """调用 TranslationMemoryManager.save_from_collection() 存词典。"""
    try:
        from transbridge.translation_memory.manager import TranslationMemoryManager
        from transbridge.translation_memory.model import SCOPE_GLOBAL

        from ._dictionary_application import dictionary_scope

        manager = TranslationMemoryManager()
        manager.load()
        source_locale, target_locale, fingerprint = dictionary_scope(args, ctx, required=False)
        namespaces = {entry.identity.namespace.value for entry in collection}
        added = manager.save_from_collection(
            collection,
            mod_file_id=args.get("mod_file_id", ""),
            scope=args.get("scope", SCOPE_GLOBAL),
            source_locale=source_locale,
            target_locale=target_locale,
            source_namespace=next(iter(namespaces)) if len(namespaces) == 1 else "",
            source_fingerprint=fingerprint,
        )
        manager.save()
        return ToolResult.ok(f"已存词典，新增 {added} 条", data={"added": added})
    except Exception as exc:
        return ToolResult.fail(f"存词典失败: {exc}", error_category="internal")


def _tool_migrate_entries(args: dict, ctx) -> ToolResult:
    """词条键对齐：当前集合 vs 上一版本集合（预留，实际由 FOMOD 流水线驱动）。"""
    return ToolResult.fail(
        "migrate_entries 需要新旧两个集合；当前无归档上下文，请通过 FOMOD 流水线调用（FR15 后置需求）",
        error_category="config",
        error_code="NOT_AVAILABLE",
    )


def _register_migrator_tools():
    from ..tool_registry import ToolRegistry

    # migrate_entries 挂 editor namespace
    ToolRegistry.register_tools(
        "editor",
        [
            {
                "name": "migrate_entries",
                "display_name": "词条键对齐迁移",
                "description": (
                    "①Align translations from an old collection to matching entry.key values in a new collection. "
                    "②Arguments: reserved. ③Returns {inherited,needs_review,missed}. ④Rule: exact key matching plus "
                    "source-change detection only; apply_dictionary owns text fallback."
                ),
                "execute": _tool_migrate_entries,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS["migrate_entries"],
            },
        ],
    )
    # 词典工具挂 translator namespace
    ToolRegistry.register_tools(
        "translator",
        [
            {
                "name": "apply_dictionary",
                "display_name": "词典套用",
                "description": (
                    "①Apply the translation-memory dictionary to fill empty translations in the current collection. "
                    "②Arguments: source_locale and target_locale unless captured in context; "
                    "optional overwrite, default false. ③Returns "
                    "{applied,key_hits,text_hits,misses,needs_review,conflicts}. "
                    "④Rule: skip disabled/wrong-language entries; stale or conflicting candidates require review."
                ),
                "execute": _tool_apply_dictionary,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS["apply_dictionary"],
            },
            {
                "name": "save_dictionary",
                "display_name": "存为词典",
                "description": (
                    "①Save translated entries from the current collection to the "
                    "translation-memory dictionary. ②Arguments: "
                    "optional mod_file_id and scope=project/global. ③Returns {added}. "
                    "④Rule: excludes locked, hidden, and "
                    "empty translations."
                ),
                "execute": _tool_save_dictionary,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS["save_dictionary"],
            },
        ],
    )


_register_migrator_tools()
