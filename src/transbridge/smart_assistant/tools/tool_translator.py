"""Assistant translation controls over detached, durable AI task results."""

from __future__ import annotations

from .base import ToolResult, require_collection, require_runtime_context
from .task_control import action_label, control_tasks, get_scoped_task_status
from .task_manager import TaskManager
from .translation_task_run import launch_translation_task


class TranslationController:
    """Configure and control the shared task runtime and recoverable AI results."""

    def __init__(self, app_context=None, task_manager=None):
        self._ctx = app_context
        self._task_mgr = task_manager

    def start_translation(self, args: dict, ctx, collection) -> ToolResult:
        mode = args.get("mode", "translate")
        if mode not in ("translate", "polish", "mixed"):
            return ToolResult.fail(f"无效模式: {mode}，可选: translate, polish, mixed")
        if mode == "polish":
            return self.start_polish(args, ctx, collection)
        entry_ids = args.get("entry_ids")
        if entry_ids is None:
            from .base import filter_entries

            scope = getattr(ctx, "translation_scope", None) or {}
            if any(scope.get(key) for key in ("stages", "labels", "categories")):
                targets = filter_entries(
                    collection,
                    {"stage": scope.get("stages"), "category": scope.get("categories"), "labels": scope.get("labels")},
                    entry_labels=getattr(ctx, "entry_labels", None),
                )
            else:
                targets = [entry for entry in collection if mode == "mixed" or entry.stage == 0]
        else:
            targets = [collection.get(key) for key in entry_ids if collection.get(key) is not None]
        if not targets:
            return ToolResult.fail("本次作用域没有可处理的词条。")
        return self._launch(args, ctx, collection, targets, mode=mode)

    def start_polish(self, args: dict, ctx, collection) -> ToolResult:
        intensity, scope = args.get("intensity", "medium"), args.get("scope", "all")
        strategy = args.get("strategy", "proofread")
        if strategy == "combined":
            strategy = "proofread"
        if scope not in ("all", "passed", "has_issues"):
            return ToolResult.fail(f"无效 scope: {scope}，可选: all, passed, has_issues")
        if intensity not in ("light", "medium", "heavy"):
            return ToolResult.fail(f"无效 intensity: {intensity}，可选: light, medium, heavy")
        if strategy not in ("proofread", "strict"):
            return ToolResult.fail(f"无效 strategy: {strategy}，可选: proofread, strict")
        entry_ids = args.get("entry_ids")
        if entry_ids is None:
            targets = [
                entry
                for entry in collection
                if entry.translation
                and (
                    scope == "all"
                    or scope == "passed"
                    and entry.stage in (1, 3, 4, 5, 6)
                    or scope == "has_issues"
                    and entry.stage == 2
                )
            ]
        else:
            targets = [collection.get(key) for key in entry_ids if collection.get(key) is not None]
        if not targets:
            return ToolResult.fail("没有可润色的词条符合本次作用域。")
        result = self._launch(
            {**args, "intensity": intensity, "strategy": strategy},
            ctx,
            collection,
            targets,
            mode="polish",
        )
        if result.success:
            result.data["scope"] = scope
        return result

    def _launch(self, args, ctx, collection, targets, *, mode):
        from transbridge.paratranz.config_manager import LLMConfig

        from .input_prerequisites import source_input_failure

        missing = source_input_failure(ctx, targets)
        if missing is not None:
            return missing

        try:
            config = LLMConfig.load_from_file()
        except Exception as exc:
            return ToolResult.fail(
                f"无法读取 LLM 配置：{exc}", error_category="config", error_code="CONFIG_LOAD_FAILED"
            )
        if not config.api_key:
            return ToolResult.fail(
                "API Key 未配置",
                error_category="config",
                error_code="API_KEY_MISSING",
                recovery_action="请在 AI 翻译设置中配置 API Key",
            )
        return launch_translation_task(
            ctx,
            collection,
            targets,
            config,
            mode=mode,
            intensity=args.get("intensity", "medium"),
            strategy=args.get("strategy", "proofread"),
            manager=self._task_mgr,
        )

    def get_task_result(self, args, ctx):
        from .translation_task_results import result_view

        try:
            return ToolResult.ok("任务成果查询完成", data=result_view(ctx, args.get("result_id")))
        except Exception as exc:
            return ToolResult.fail(str(exc), error_code="TASK_RESULT_UNAVAILABLE")

    def merge_task_result(self, args, ctx):
        from .translation_task_merge import merge_saved_results
        from .translation_task_results import load_task_result

        result_id = args.get("result_id")
        if not result_id:
            return ToolResult.fail("请指定 result_id。")
        try:
            store, record, payload = load_task_result(ctx, result_id)
            summary, warning = merge_saved_results(ctx, store, record, payload, policy=args.get("policy"))
            return ToolResult(
                success=True,
                partial=record.get("parse_outcome") == "partial" or bool(summary.get("pending_count")),
                message="已合入保存的任务成果",
                data={
                    "result_id": result_id,
                    "merge": summary,
                    "parse_outcome": record.get("parse_outcome"),
                    "parse_diagnostics": record.get("parse_diagnostics", []),
                },
                warnings=[warning] if warning else None,
            )
        except Exception as exc:
            return ToolResult.fail(str(exc), data={"result_id": result_id}, error_code="TASK_RESULT_MERGE_FAILED")

    def stop_task(self, args: dict, ctx) -> ToolResult:
        """停止/暂停/恢复当前调用者拥有的任务。"""
        return control_tasks(args, ctx, self._task_mgr or TaskManager())

    def _action_label(self, action: str) -> str:
        """action → 中文标签。"""
        return action_label(action)

    # ── 查询状态 ──────────────────────────────────────────────────

    def get_task_status(self, args: dict, ctx) -> ToolResult:
        """查询当前调用者会话内的翻译任务状态。"""
        return get_scoped_task_status(args, ctx, self._task_mgr or TaskManager())

    def get_translation_config(self, args: dict, ctx) -> ToolResult:
        from .translation_settings import TranslationSettings

        return TranslationSettings().get_translation_config(args, ctx)

    def set_translation_config(self, args: dict, ctx) -> ToolResult:
        from .translation_settings import TranslationSettings

        return TranslationSettings().set_translation_config(args, ctx)

    def set_term_config(self, args: dict, ctx) -> ToolResult:
        from .translation_settings import TranslationSettings

        return TranslationSettings().set_term_config(args, ctx)

    def set_scope(self, args: dict, ctx) -> ToolResult:
        from .translation_settings import TranslationSettings

        return TranslationSettings().set_scope(args, ctx)

    def get_scope_preview(self, args: dict, ctx) -> ToolResult:
        from .translation_settings import TranslationSettings

        return TranslationSettings().get_scope_preview(args, ctx)


# ═══════════════════════════════════════════════════════════════════
#  无状态 controller + 模块级 wrapper（保持向后兼容）
# ═══════════════════════════════════════════════════════════════════

_translator_ctrl = TranslationController()


# ── 模块级 wrapper 函数（@require_collection 装饰在此处，因装饰器不支持实例方法） ──


@require_runtime_context
@require_collection
def _tool_start_translation(args: dict, ctx, collection) -> ToolResult:
    return _translator_ctrl.start_translation(args, ctx, collection)


@require_runtime_context
@require_collection
def _tool_start_polish(args: dict, ctx, collection) -> ToolResult:
    return _translator_ctrl.start_polish(args, ctx, collection)


@require_runtime_context
def _tool_stop_task(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.stop_task(args, ctx)


@require_runtime_context
def _tool_get_task_status(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.get_task_status(args, ctx)


@require_runtime_context
def _tool_get_task_result(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.get_task_result(args, ctx)


@require_runtime_context
def _tool_merge_task_result(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.merge_task_result(args, ctx)


@require_runtime_context
def _tool_get_translation_config(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.get_translation_config(args, ctx)


@require_runtime_context
def _tool_set_translation_config(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.set_translation_config(args, ctx)


@require_runtime_context
def _tool_set_term_config(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.set_term_config(args, ctx)


@require_runtime_context
def _tool_set_scope(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.set_scope(args, ctx)


@require_runtime_context
def _tool_get_scope_preview(args: dict, ctx) -> ToolResult:
    return _translator_ctrl.get_scope_preview(args, ctx)


# ═══════════════════════════════════════════════════════════════════
#  参数 Schema
# ═══════════════════════════════════════════════════════════════════

_PARAM_SCHEMAS = {
    "start_translation": {
        "mode": {
            "type": "str",
            "required": False,
            "description": "Translation mode: translate/polish/mixed; default translate",
        },
        "entry_ids": {
            "type": "list",
            "required": False,
            "description": "Target entry IDs; defaults to all untranslated entries",
        },
    },
    "start_polish": {
        "entry_ids": {"type": "list", "required": False, "description": "Entry IDs to polish; provide this or scope"},
        "scope": {
            "type": "str",
            "required": False,
            "description": (
                "Polish scope: all (all translated), passed (stage 1/3/4/5/6), or has_issues (stage 2); default all"
            ),
        },
        "intensity": {
            "type": "str",
            "required": False,
            "description": "Polish intensity: light/medium/heavy; default medium",
        },
        "strategy": {
            "type": "str",
            "required": False,
            "description": "Proofreading strategy: proofread (default) or strict (multi-stage compatible)",
        },
    },
    "stop_task": {
        "task_id": {
            "type": "str",
            "required": False,
            "description": "Session task ID; omit only for a unique active root task (plan children excluded)",
        },
        "all_tasks": {
            "type": "bool",
            "required": False,
            "description": "Explicitly target all active tasks in current session; never combine with task_id",
        },
        "action": {"type": "str", "required": False, "description": "Action: stop (default), pause, or resume"},
    },
    "get_task_status": {
        "task_id": {
            "type": "str",
            "required": False,
            "description": "Task ID; omitted returns summaries for all tasks",
        },
    },
    "get_task_result": {
        "result_id": {
            "type": "str",
            "required": False,
            "description": "Durable result ID; omit to list session results",
        },
    },
    "merge_task_result": {
        "result_id": {
            "type": "str",
            "required": True,
            "description": "Saved task result ID; never repeats computation or remote reads",
        },
        "policy": {
            "type": "str",
            "required": False,
            "description": "Conflict policy: keep_current/prefer_incoming/defer; default is the frozen AI preference",
        },
    },
    # Story 09: 翻译配置
    "get_translation_config": {},
    "set_translation_config": {
        "provider": {
            "type": "str",
            "required": False,
            "description": "Must be provided together with base_url and model",
        },
        "base_url": {
            "type": "str",
            "required": False,
            "description": "Must be provided together with provider and model",
        },
        "model": {"type": "str", "required": False, "description": "Model name"},
        "temperature": {"type": "float", "required": False, "description": "Generation temperature"},
        "max_tokens": {"type": "int", "required": False, "description": "Maximum output tokens"},
        "target_lang": {"type": "str", "required": False, "description": "Target language code"},
        "game_profile": {"type": "str", "required": False, "description": "Game profile"},
    },
    "set_scope": {
        "stages": {"type": "list", "required": False, "description": "Target stages"},
        "labels": {"type": "list", "required": False, "description": "Target labels"},
        "categories": {"type": "list", "required": False, "description": "Target categories"},
        "action": {
            "type": "str",
            "required": False,
            "description": "Scope action: include/exclude/only; default include",
        },
    },
    "get_scope_preview": {},
    # Story 24: 术语配置
    "set_term_config": {
        "term_sources": {
            "type": "list",
            "required": False,
            "description": "Terminology source priority list: dynamic/paratranz/json/csv/excel",
        },
        "json_path": {"type": "str", "required": False, "description": "Local JSON glossary path"},
        "csv_path": {"type": "str", "required": False, "description": "Local CSV glossary path"},
        "excel_path": {"type": "str", "required": False, "description": "Local Excel glossary path"},
    },
}


# ═══════════════════════════════════════════════════════════════════
#  工具注册
# ═══════════════════════════════════════════════════════════════════


def _register_translator_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "translator",
        [
            {
                "name": "start_translation",
                "display_name": "启动翻译",
                "description": (
                    "①Start an AI translation background task. ②Arguments: mode=translate (default)/polish/mixed, "
                    "optional entry_ids; without entry_ids use set_scope, defaulting to stage 0. "
                    "③Returns {task_id, mode}. "
                    "④Rules: configure the API key and inspect get_translation_config first; "
                    "parallel tasks are allowed."
                ),
                "execute": _tool_start_translation,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("start_translation", {}),
            },
            {
                "name": "start_polish",
                "display_name": "启动校对",
                "description": (
                    "①Start an AI proofreading background task. ②Provide entry_ids or scope; entry_ids wins. "
                    "scope=all/passed(stage 1/3/4/5/6)/has_issues(stage 2), intensity=light/medium/heavy, "
                    "strategy=proofread/strict. "
                    "③Returns {task_id, strategy, intensity, scope, entry_count}. "
                    "④Rule: configure "
                    "the API key and inspect get_translation_config first."
                ),
                "execute": _tool_start_polish,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS.get("start_polish", {}),
            },
            {
                "name": "stop_task",
                "display_name": "停止/暂停/恢复",
                "description": (
                    "①Stop, pause, or resume background tasks in the current session. "
                    "②Optional task_id selects one task; omit only when there is one active root task. "
                    "Children of an active plan are controlled through that plan unless explicitly selected. "
                    "Use all_tasks=true only when the user explicitly requests all tasks; "
                    "action=stop (default)/pause/resume. ③Returns affected_task_ids, action and task_states. "
                    "④A cancelling task has received the signal but has not stopped yet. "
                    "Narrow stop needs no additional confirmation; batch/pause/resume require confirmation."
                ),
                "execute": _tool_stop_task,
                "permission": "write",
                "require_confirmation": False,
                "parameters": _PARAM_SCHEMAS.get("stop_task", {}),
            },
            {
                "name": "get_task_status",
                "display_name": "查询任务状态",
                "description": (
                    "①Query task progress. ②Optional task_id. ③One task returns "
                    "{task_id,status,progress{current,total,message},created_at,metadata}; all tasks return "
                    "{active_count,total_count,tasks[{task_id,status,metadata}]}. ④status is "
                    "running/paused/completed/cancelled/failed."
                ),
                "execute": _tool_get_task_status,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_task_status", {}),
            },
            {
                "name": "get_task_result",
                "display_name": "查询任务成果",
                "description": "Read saved task results and per-entry evidence in the current caller session.",
                "execute": _tool_get_task_result,
                "permission": "read",
                "request_scope": "target",
                "parameters": _PARAM_SCHEMAS["get_task_result"],
            },
            {
                "name": "merge_task_result",
                "display_name": "重新合入任务成果",
                "description": (
                    "Merge saved AI, dictionary or parser results into their original version "
                    "without repeating computation."
                ),
                "execute": _tool_merge_task_result,
                "permission": "write",
                "require_confirmation": True,
                "parameters": _PARAM_SCHEMAS["merge_task_result"],
            },
            # Story 09: 翻译配置
            {
                "name": "get_translation_config",
                "display_name": "翻译配置",
                "description": (
                    "①Return a read-only unified LLM configuration snapshot. ②No arguments. "
                    "③Returns non-secret fields such as provider/base_url_host/model/config_revision. "
                    "④Rule: never returns plaintext credentials."
                ),
                "execute": _tool_get_translation_config,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_translation_config", {}),
            },
            {
                "name": "set_translation_config",
                "display_name": "设置翻译配置",
                "description": (
                    "①Update the unified LLM configuration. ②provider/base_url/model must be supplied together; "
                    "temperature/max_tokens/target_lang/game_profile are optional. "
                    "③Returns changed_fields/config_revision."
                ),
                "execute": _tool_set_translation_config,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_translation_config", {}),
            },
            {
                "name": "set_scope",
                "display_name": "设置作用域",
                "description": (
                    "①Set the default translation scope used by start_translation without entry_ids. "
                    "②Arguments: stages, "
                    "labels, categories, action=include/exclude/only. ③Returns a scope snapshot. ④Rules: AND across "
                    "dimensions, OR within one; verify labels/categories with "
                    "list_labels/get_statistics; include and only "
                    "currently behave the same."
                ),
                "execute": _tool_set_scope,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_scope", {}),
            },
            {
                "name": "get_scope_preview",
                "display_name": "作用域预览",
                "description": (
                    "①Preview counts for the current scope. ②No arguments. ③Returns "
                    "{matched,total,scope{stages,labels,categories,action}}. "
                    "④Rules: returns counts, not entries; default "
                    "scope is untranslated stage 0; call after set_scope to confirm the range."
                ),
                "execute": _tool_get_scope_preview,
                "permission": "read",
                "parameters": _PARAM_SCHEMAS.get("get_scope_preview", {}),
            },
            {
                "name": "set_term_config",
                "display_name": "术语配置",
                "description": (
                    "①Set terminology-source priority and local paths. ②Arguments: ordered term_sources "
                    "(dynamic/paratranz/json/csv/excel), json_path, csv_path, excel_path. "
                    "dynamic is always available for extraction during AI translation. ③Returns {changed}. ④Rules: "
                    "inspect get_translation_config first; an empty list disables all sources; "
                    "invalid source names are "
                    "rejected."
                ),
                "execute": _tool_set_term_config,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_term_config", {}),
            },
        ],
    )


_register_translator_tools()
