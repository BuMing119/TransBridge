# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""助手词典套用与保存工具。"""

from __future__ import annotations

from .base import ToolResult, validate_params
from .diagnostics import technical_error

_SAVE_PARAMETERS = {
    "mod_file_id": {"type": "str", "required": False, "description": "Dictionary name; defaults to the source stem"},
    "scope": {"type": "str", "enum": ["project", "global"], "required": False, "description": "Defaults to global"},
    "selected_only": {"type": "bool", "required": False, "description": "Save only the workbench selection"},
    "tags": {"type": "list", "required": False, "description": "Dictionary tags for the saved translations"},
}


def _tool_apply_dictionary(args: dict, ctx) -> ToolResult:
    """Fill empty translations using the desktop dictionary command."""
    if args:
        return ToolResult.fail("套用词典不接受参数。", error_category="input", error_code="UNKNOWN_FIELD")
    try:
        from ._dictionary_application import apply_dictionary

        return apply_dictionary(ctx)
    except Exception as exc:
        return ToolResult.fail(f"词典套用失败: {technical_error(exc)}", error_category="internal")


@validate_params(_SAVE_PARAMETERS)
def _tool_save_dictionary(args: dict, ctx) -> ToolResult:
    """Publish the same selected translations as the workbench dictionary dialog."""
    try:
        from transbridge.application.projects.dictionary_apply import dictionary_mod_id
        from transbridge.translation_memory.manager import TranslationMemoryManager
        from transbridge.translation_memory.model import SCOPE_GLOBAL
        from transbridge.translation_memory.publication import capture_dictionary_save, publish_dictionary

        from .types import ExecutionContext
        from .workbench_rows import check_workbench_source

        execution = ctx if isinstance(ctx, ExecutionContext) else ExecutionContext(app_context=ctx)
        app = execution.app_context
        if app is None:
            return ToolResult.fail("当前没有加载翻译集合", error_category="input", error_code="COLLECTION_NOT_LOADED")
        gate, effect_id = execution.assistant_gate, execution.assistant_effect_id
        if (gate is None and (effect_id or execution.assistant_required)) or (gate is not None and not effect_id):
            return ToolResult.fail("请求执行许可缺失。", error_category="permission", error_code="TURN_LEASE_STALE")
        token = getattr(execution, "cancellation_token", None)
        ui_context = ExecutionContext(app_context=app)
        warnings = []

        def checkpoint():
            if token is not None:
                token.raise_if_cancelled()

        def check_source():
            check_workbench_source(execution, app)
            if execution._target_variant_revision != getattr(
                app, "variant_revision", None
            ) or execution._target_dictionary_mod_id != dictionary_mod_id(app):
                raise ValueError("词条在准备期间已变化，请重新存为词典。")
            checkpoint()

        def inputs():
            check_source()
            collection = execution.__dict__.get("collection", execution._target_collection)
            if collection is None:
                return None
            selected = (
                (entry.identity for entry in getattr(execution, "selected_entries", ()))
                if args.get("selected_only")
                else None
            )
            return capture_dictionary_save(
                collection,
                args.get("mod_file_id", execution._target_dictionary_mod_id),
                args.get("scope", SCOPE_GLOBAL),
                selected,
                args.get("tags", []),
            )

        def commit(publish):
            ui_context.safe_mutate_wait(check_source)
            if gate is None:
                publish()
                return
            committed = False

            def apply():
                nonlocal committed
                publish()
                committed = True

            try:
                decision = gate.commit(effect_id, apply)
                if not decision.accepted:
                    raise RuntimeError(f"词典提交请求被拒绝：{decision.reason}")
            except Exception as exc:
                if not committed:
                    raise
                warnings.append(f"词典操作已提交，助手回执未确认：{technical_error(exc)}")

        save = ui_context.safe_mutate_wait(inputs)
        if save is None:
            return ToolResult.fail("当前没有加载翻译集合", error_category="input", error_code="COLLECTION_NOT_LOADED")
        added, _ = publish_dictionary(TranslationMemoryManager(), save, commit=commit, checkpoint=checkpoint)
        return ToolResult.ok(f"已存词典，新增 {added} 条", data={"added": added}, warnings=warnings or None)
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
                    "Fill empty translations with the desktop dictionary command. The current mod dictionary has "
                    "priority. The workbench shows conflicting translations for review before applying the batch. "
                    "Returns counts for applied, key_hits, text_hits, misses, needs_review and conflicts."
                ),
                "execute": _tool_apply_dictionary,
                "permission": "write",
                "parameters": {},
            },
            {
                "name": "save_dictionary",
                "display_name": "存为词典",
                "description": (
                    "①Save translated entries from the current collection to the "
                    "translation-memory dictionary. ②Arguments: "
                    "optional mod_file_id, scope=project/global, selected_only and tags. ③Returns {added}. "
                    "④Rule: excludes locked, hidden, and "
                    "empty translations."
                ),
                "execute": _tool_save_dictionary,
                "permission": "write",
                "parameters": _SAVE_PARAMETERS,
            },
        ],
    )


_register_dictionary_tools()
