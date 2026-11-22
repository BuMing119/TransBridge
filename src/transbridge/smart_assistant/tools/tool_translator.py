# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Assistant translation controls over detached, durable AI task results."""

from __future__ import annotations

from transbridge.application.preflight.ai import AiPreflightCode, preflight_ai_run
from transbridge.application.translation.task_scope import task_entry_groups
from transbridge.config.translation_preferences import TERM_SOURCES

from .base import ToolResult, require_collection, require_runtime_context
from .task_control import action_label, control_tasks, get_scoped_task_status
from .task_manager import TaskManager
from .translation_options import external_intensity, translation_config
from .translation_scope import translation_candidates
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
        return self._start(args, ctx, collection, mode=mode)

    def start_polish(self, args: dict, ctx, collection) -> ToolResult:
        result = self._start(args, ctx, collection, mode="polish")
        if result.success and "scope" in args:
            result.data["scope"] = args["scope"]
        return result

    def _start(self, args, ctx, collection, *, mode):
        try:
            _, targets, memberships = translation_candidates(args, ctx, collection, mode=mode)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        if not targets:
            return ToolResult.fail(
                "没有可润色的词条符合本次作用域。" if mode == "polish" else "本次作用域没有可处理的词条。"
            )
        return self._launch(args, ctx, collection, targets, memberships, mode=mode)

    def _launch(self, args, ctx, collection, targets, memberships, *, mode):
        from .input_prerequisites import source_input_failure

        missing = source_input_failure(ctx, targets)
        if missing is not None:
            return missing

        try:
            config = translation_config(mode, args)
        except Exception as exc:
            return ToolResult.fail(
                f"无法读取 LLM 配置：{exc}", error_category="config", error_code="CONFIG_LOAD_FAILED"
            )
        groups = task_entry_groups(targets, mode=mode, config=config, memberships=memberships)
        if not (groups.translate or groups.polish):
            return ToolResult.fail("本次作用域没有可处理的词条。")
        preflight = preflight_ai_run(
            mode, config, (*groups.translate, *groups.polish), mixed_has_translation=bool(groups.translate)
        )
        if preflight.blocking_issues:
            issue = preflight.blocking_issues[0]
            return ToolResult.fail(
                preflight.blocking_reason,
                error_category="config",
                error_code="API_KEY_MISSING"
                if issue.code is AiPreflightCode.MISSING_API_KEY
                else issue.code.value.upper(),
                recovery_action="请检查 AI 翻译设置和运行条件。",
            )
        return launch_translation_task(
            ctx,
            collection,
            groups,
            config,
            mode=mode,
            profile=args.get("profile"),
            intensity=external_intensity(config.pp_polish_level),
            strategy=config.pp_strategy,
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
            message = {
                "staged-unsaved": "任务成果已应用，等待项目保存",
                "acknowledgement-pending": "任务应用已处理，回执待同步",
                "conflicted": "任务成果存在待处理的冲突",
                "discarded-or-superseded": "原应用已撤销或被后续编辑替换",
            }.get(summary.get("application_state"), "已合入保存的任务成果")
            return ToolResult(
                success=True,
                partial=record.get("parse_outcome") == "partial" or bool(summary.get("pending_count")),
                message=message,
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

_WORKFLOW_PROFILE = {
    "type": "str",
    "required": False,
    "description": "Saved desktop workflow name or ID with the same base mode; omit to use the mode preset",
}

_PARAM_SCHEMAS = {
    "start_translation": {
        "profile": _WORKFLOW_PROFILE,
        "mode": {
            "type": "str",
            "required": False,
            "description": "Translation mode: translate/polish/mixed; default translate",
        },
        "entry_ids": {
            "type": "list",
            "required": False,
            "description": "Explicit entry IDs; omit to use the saved translation scope or mode default",
        },
    },
    "start_polish": {
        "profile": _WORKFLOW_PROFILE,
        "entry_ids": {"type": "list", "required": False, "description": "Entry IDs to polish; omit to use the scope"},
        "scope": {
            "type": "str",
            "required": False,
            "description": "Use all to include every eligible translated stage within the configured scope",
        },
        "intensity": {
            "type": "str",
            "required": False,
            "description": "Override the saved polish intensity for this task: light/medium/heavy",
        },
        "strategy": {
            "type": "str",
            "required": False,
            "description": "Override the saved polish strategy for this task: proofread or strict",
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
            "description": "openai_compatible or anthropic; supply together with base_url and model",
            "enum": ["openai_compatible", "anthropic"],
        },
        "base_url": {
            "type": "str",
            "required": False,
            "description": "Must be provided together with provider and model",
        },
        "model": {"type": "str", "required": False, "description": "Model name"},
        "temperature": {"type": "float", "min": 0, "max": 2, "description": "Temperature, up to two decimal places"},
        "max_tokens": {"type": "int", "min": 0, "max": 1000000, "description": "Output token limit, 0=auto"},
        "target_lang": {"type": "str", "required": False, "description": "Target language code"},
    },
    "set_scope": {
        "stages": {"type": "list", "required": False, "description": "Target stages"},
        "labels": {"type": "list", "required": False, "description": "Target labels"},
        "categories": {"type": "list", "required": False, "description": "Target categories"},
    },
    "get_scope_preview": {
        "profile": _WORKFLOW_PROFILE,
        "mode": {"type": "str", "required": False, "description": "translate (default), mixed, or polish"},
        "entry_ids": {"type": "list", "required": False, "description": "Explicit target IDs, as at launch"},
        "scope": {"type": "str", "required": False, "description": "For polish, all selects all eligible stages"},
    },
    # Story 24: 术语配置
    "set_term_config": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "term_sources": {
                "type": "array",
                "description": "All five sources in priority order, once each",
                "minItems": len(TERM_SOURCES),
                "maxItems": len(TERM_SOURCES),
                "uniqueItems": True,
                "items": {"type": "string", "enum": list(TERM_SOURCES)},
            },
            "json_path": {"type": "string", "description": "Local JSON glossary path"},
            "csv_path": {"type": "string", "description": "Local CSV glossary path"},
            "excel_path": {"type": "string", "description": "Local Excel glossary path"},
        },
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
                    "optional entry_ids; omit to use the saved scope. Without a saved scope, translate uses stage 0, "
                    "polish uses stages 1/2/3/5, and mixed applies the configured action rules to all eligible stages. "
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
                    "①Start an AI proofreading background task. ②Optional entry_ids or the saved translation scope. "
                    "Default stages 1/2/3/5; scope=all includes all eligible stages within the saved scope. "
                    "intensity=light/medium/heavy, "
                    "strategy=proofread/strict. "
                    "③Returns {task_id, strategy, intensity, scope, entry_count}. "
                    "Omitted intensity and strategy use the saved desktop polish preset."
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
                    "queued/running/paused/cancelling/cancelled/completed/failed."
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
                    "Apply saved translation candidates owned by this session to their original project version "
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
                    "temperature/max_tokens/target_lang are optional. "
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
                    "labels, categories. ③Returns a scope snapshot. ④Rules: AND across "
                    "dimensions, OR within one. Verify labels/categories with list_labels/get_statistics."
                ),
                "execute": _tool_set_scope,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_scope", {}),
            },
            {
                "name": "get_scope_preview",
                "display_name": "作用域预览",
                "description": (
                    "①Preview the actual task range. ②Optional mode, entry_ids and polish scope. ③Returns "
                    "{matched,total,scope{stages,labels,categories}}. "
                    "④Defaults: translate uses stage 0, polish uses stages 1/2/3/5, mixed applies configured rules "
                    "to all eligible stages. Explicit entry_ids select those identities. Use after set_scope to verify."
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
                    "③Returns {changed}. ④Rules: inspect get_translation_config first. "
                    "To reorder, include each of the five sources exactly once."
                ),
                "execute": _tool_set_term_config,
                "permission": "write",
                "parameters": _PARAM_SCHEMAS.get("set_term_config", {}),
            },
        ],
    )


_register_translator_tools()
