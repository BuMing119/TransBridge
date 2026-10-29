"""Preset-aware post-processing tools for Smart Assistant."""

from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
import threading

from .base import ToolResult, require_runtime_context

logger = logging.getLogger(__name__)

_last_reports: dict[tuple, dict] = {}
_last_report_lock = threading.Lock()


def _report_scope(ctx) -> tuple:
    from .translation_task_results import _caller

    app, context = _caller(ctx)
    identity = getattr(ctx, "_target_version_identity", None) or getattr(app, "active_version_identity", None)
    return context.owner_id, context.session_id, identity


def set_last_report(report: dict | None, ctx) -> None:
    """Thread-safe write used by post-process and polish tools."""

    scope = _report_scope(ctx)
    with _last_report_lock:
        if report is None:
            _last_reports.pop(scope, None)
        else:
            _last_reports[scope] = deepcopy(report)


def get_last_report(ctx) -> dict | None:
    """Return the most recent canonical post-process summary."""

    scope = _report_scope(ctx)
    with _last_report_lock:
        return deepcopy(_last_reports.get(scope))


def _resolve_report_directory(ctx) -> Path:
    from transbridge.paratranz.config_manager import LLMConfig, ParatranzConfig

    snapshot = getattr(getattr(ctx, "active_slot", None), "source_snapshot", None)
    source = getattr(snapshot, "source", None)
    name = getattr(source, "display_name", None) or getattr(source, "uri", None) or getattr(ctx, "esp_path", None)
    if name:
        return Path(LLMConfig.get_ai_translator_dir(Path(name).stem)) / "reports"
    return Path(ParatranzConfig.get_data_dir()) / "reports" / "postprocess"


class ProofreaderController:
    """Coordinate proofreader tools without owning execution details."""

    def __init__(self, app_context=None, task_manager=None):
        self._ctx = app_context
        self._task_mgr = task_manager

    def run_postprocess(self, args: dict, ctx) -> ToolResult:
        """Start one preset-aware Proofread or strict post-process task."""

        from .postprocess_task_run import launch_postprocess_task

        return launch_postprocess_task(
            args,
            ctx,
            report_directory=_resolve_report_directory,
            report_callback=set_last_report,
            manager=self._task_mgr,
        )

    def get_quality_report(self, args: dict, ctx) -> ToolResult:
        """Return the most recent post-process or polish report."""

        try:
            report = get_last_report(ctx)
        except ValueError as exc:
            return ToolResult.fail(str(exc), error_code="TASK_RESULT_SCOPE_REQUIRED")
        if report is None:
            return ToolResult.ok("暂无质量报告", data={"reports": []})
        phase = report.get("phase", "postprocess")
        if phase == "polish":
            lines = [
                f"最近润色报告: 条目{report.get('entry_count', '?')}条, "
                f"润色级别{report.get('polish_level', '?')}, 范围{report.get('scope', '?')}, "
                f"变更总计{report.get('total', '?')}处"
            ]
        else:
            lines = [
                f"最近报告: 策略{report.get('strategy', '?')}, 检查{report.get('total_checked', '?')}条, "
                f"发现问题{report.get('issue_count', '?')}个, 自动修复{report.get('auto_fixed', '?')}个"
            ]
            verdicts = report.get("verdict_stats", {})
            if verdicts:
                lines.append(
                    f"结果: 通过{verdicts.get('passed', 0)}/"
                    f"拒绝{verdicts.get('rejected', 0)}/待审{verdicts.get('pending', 0)}"
                )
        if report.get("report_file"):
            lines.append(f"报告文件: {report['report_file']}")
        return ToolResult.ok(" | ".join(lines), data={"reports": [report]})

    def list_quality_reports(self, args: dict, ctx) -> ToolResult:
        """List historical post-process Excel reports."""

        reports_dir = _resolve_report_directory(ctx)
        limit = args.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return ToolResult.fail("limit 必须是正整数")
        files = []
        try:
            if reports_dir.is_dir():
                entries = sorted(
                    (path for path in reports_dir.iterdir() if path.suffix == ".xlsx"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                for path in entries[:limit]:
                    stat = path.stat()
                    files.append({"name": path.name, "size": stat.st_size, "modified_at": stat.st_mtime})
        except OSError as exc:
            logger.warning("扫描报告目录失败: %s", exc)
        return ToolResult.ok(f"共 {len(files)} 份报告" if files else "暂无历史报告", data={"files": files})


_proofreader_ctrl = ProofreaderController()


@require_runtime_context
def _tool_run_postprocess(args: dict, ctx) -> ToolResult:
    return _proofreader_ctrl.run_postprocess(args, ctx)


@require_runtime_context
def _tool_get_quality_report(args: dict, ctx) -> ToolResult:
    return _proofreader_ctrl.get_quality_report(args, ctx)


@require_runtime_context
def _tool_list_quality_reports(args: dict, ctx) -> ToolResult:
    return _proofreader_ctrl.list_quality_reports(args, ctx)


def _register_proofreader_tools() -> None:
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "proofreader",
        [
            {
                "name": "run_postprocess",
                "display_name": "校对 / 严格后处理",
                "description": (
                    "Run proofreading with the built-in polish preset by default. profile may be "
                    "translate/polish/mixed, custom (the selected named configuration), or a named configuration "
                    "name/UUID; strategy is proofread or strict. entry_ids takes priority; otherwise scope is "
                    "configured/set_scope/all/passed/has_issues. intensity is "
                    "configured/light/medium/heavy. In strict mode, "
                    "phases may select consistency/format/quality_gate/refinement/"
                    "polish/arbitration; legacy calls supplying only phases automatically use strict. max_concurrent, "
                    "max_tokens_per_batch, max_output_tokens, and max_terms_per_batch may be overridden; max_workers "
                    "remains a "
                    "legacy concurrency alias. Runs in the background and requires user confirmation."
                ),
                "execute": _tool_run_postprocess,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": {
                    "profile": {
                        "type": "str",
                        "required": False,
                        "description": "Preset or named workflow; default polish",
                    },
                    "strategy": {
                        "type": "str",
                        "required": False,
                        "description": "proofread or strict; default proofread",
                    },
                    "phases": {
                        "type": "list",
                        "required": False,
                        "description": "Strict-mode phases; supplying them selects strict automatically",
                    },
                    "entry_ids": {"type": "list", "required": False, "description": "Entry keys to process first"},
                    "scope": {
                        "type": "str",
                        "required": False,
                        "description": "configured/set_scope/all/passed/has_issues",
                    },
                    "intensity": {"type": "str", "required": False, "description": "configured/light/medium/heavy"},
                    "max_concurrent": {
                        "type": "int",
                        "required": False,
                        "description": "Shared maximum in-flight requests (1-128)",
                    },
                    "max_tokens_per_batch": {
                        "type": "int",
                        "required": False,
                        "description": "Maximum business-content tokens per request",
                    },
                    "max_output_tokens": {
                        "type": "int",
                        "required": False,
                        "description": "Maximum output tokens; 0 omits the value when supported by the provider",
                    },
                    "max_terms_per_batch": {
                        "type": "int",
                        "required": False,
                        "description": "Maximum terminology entries injected per item",
                    },
                    "max_workers": {"type": "int", "required": False, "description": "Legacy concurrency alias (1-8)"},
                },
            },
            {
                "name": "get_quality_report",
                "display_name": "质量报告",
                "description": "Return the latest run_postprocess or start_polish report summary; no arguments.",
                "execute": _tool_get_quality_report,
                "permission": "read",
                "parameters": {},
            },
            {
                "name": "list_quality_reports",
                "display_name": "历史报告",
                "description": "List historical post-processing Excel reports; optional limit, default 50.",
                "execute": _tool_list_quality_reports,
                "permission": "read",
                "parameters": {
                    "limit": {"type": "int", "required": False, "description": "Maximum number of reports to return"}
                },
            },
        ],
    )


_register_proofreader_tools()
