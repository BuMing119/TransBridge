# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""助手词典套用与保存工具。"""

from __future__ import annotations

from .base import ToolResult, validate_params
from .diagnostics import technical_error

_SAVE_PARAMETERS = {
    "selected_only": {"type": "bool", "required": False, "description": "Save only the workbench selection"},
}


def _tool_apply_dictionary(args: dict, ctx) -> ToolResult:
    """Fill empty translations from the workbench dictionary library."""
    if args:
        return ToolResult.fail("套用词典不接受参数。", error_category="input", error_code="UNKNOWN_FIELD")
    try:
        from ._dictionary_application import apply_dictionary

        return apply_dictionary(ctx)
    except Exception as exc:
        return ToolResult.fail(f"词典套用失败: {technical_error(exc)}", error_category="internal")


@validate_params(_SAVE_PARAMETERS)
def _tool_save_dictionary(args: dict, ctx) -> ToolResult:
    """Save translations from the current workbench source to the personal library."""
    try:
        from ._dictionary_application import save_dictionary

        return save_dictionary(ctx, selected_only=args.get("selected_only", False))
    except ValueError as exc:
        return ToolResult.fail(str(exc), error_category="input")
    except Exception as exc:
        return ToolResult.fail(f"存词典失败: {technical_error(exc)}", error_category="internal")


def _register_dictionary_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "translator",
        [
            {
                "name": "apply_dictionary",
                "display_name": "词典套用",
                "description": (
                    "Fill only untranslated (stage 0) entries with empty translations using the personal dictionary "
                    "and active main dictionary. The workbench determines the game, languages and matching "
                    "priority automatically. Returns counts for applied, key_hits, text_hits and misses."
                ),
                "execute": _tool_apply_dictionary,
                "permission": "write",
                "parameters": {},
            },
            {
                "name": "save_dictionary",
                "display_name": "存为词典",
                "description": (
                    "Save translated entries from the current workbench source to the personal dictionary. "
                    "The workbench determines the source, game and languages automatically. Set selected_only "
                    "to save only selected entries. Excludes locked, hidden and empty translations. Returns {saved}."
                ),
                "execute": _tool_save_dictionary,
                "permission": "write",
                "parameters": _SAVE_PARAMETERS,
            },
        ],
    )


_register_dictionary_tools()
