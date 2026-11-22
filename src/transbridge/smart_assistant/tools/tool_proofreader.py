# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read task quality summaries and the desktop's exported report catalog."""

from __future__ import annotations

from dataclasses import asdict

from .base import ToolResult, require_runtime_context


class ProofreaderController:
    def get_quality_report(self, args: dict, ctx) -> ToolResult:
        from transbridge.application.tasks.history_service import load_candidate_report
        from transbridge.application.translation.task_history import restore_report_snapshot
        from transbridge.ui.tools.ai_translator.reporting import report_overview

        from .translation_task_results import _caller, _recovery_view, _result_location

        try:
            app, context = _caller(ctx)
        except ValueError as exc:
            return ToolResult.fail(str(exc), error_code="TASK_RESULT_SCOPE_REQUIRED")
        identity = getattr(app, "active_version_identity", None)
        if getattr(ctx, "_target_version_identity", identity) != identity:
            return ToolResult.fail("工程版本已切换，请重新查询报告。", error_code="TASK_RESULT_SCOPE_REQUIRED")
        if identity is None:
            return ToolResult.ok("暂无质量报告", data={"reports": []})
        try:
            store = _result_location(app)
            recovery = _recovery_view(store, context)
            records = recovery.list_summaries(
                project_id=identity[0], variant_id=identity[1], states=("completed", "partial", "failed", "cancelled")
            )
            record = next(
                (
                    item
                    for item in records
                    if item.get("origin") == "smart-assistant"
                    and item.get("mode") in {"translate", "polish", "mixed", "postprocess"}
                ),
                None,
            )
            if record is None:
                return ToolResult.ok("暂无质量报告", data={"reports": []})
            result_id = record["task_id"]
            history = store.history(owner_id=context.owner_id, session_id=context.session_id)
            report = (
                history.load(result_id) if history.contains(result_id) else load_candidate_report(recovery, result_id)
            )
            summaries = []
            for source in report["sources"]:
                if not source.get("snapshot"):
                    continue
                snapshot = restore_report_snapshot(source["snapshot"])
                summaries.append({
                    "source": source["label"],
                    "input": snapshot.input_count,
                    "accepted": snapshot.accepted_count,
                    "issues": snapshot.issue_count,
                    "failures": snapshot.failure_count,
                    **asdict(report_overview(snapshot)),
                })
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return ToolResult.fail(f"无法读取任务质量报告：{exc}", error_code="TASK_RESULT_UNAVAILABLE")
        return ToolResult.ok(
            "已读取任务质量报告" if summaries else "该任务没有已保存的质量报告条目",
            data={
                "result_id": result_id,
                "state": record["state"],
                "error": record.get("error", ""),
                "reports": summaries,
            },
        )

    def list_quality_reports(self, args: dict, ctx) -> ToolResult:
        """List the newest workbooks shown in the desktop report history."""
        from transbridge.ai_translator.report_history import list_exported_reports

        from .translation_task_results import _caller

        limit = args.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            return ToolResult.fail("limit 必须是正整数")
        try:
            _caller(ctx)
        except ValueError as exc:
            return ToolResult.fail(str(exc), error_code="TASK_RESULT_SCOPE_REQUIRED")
        try:
            reports = list_exported_reports()
        except OSError as exc:
            return ToolResult.fail(f"无法读取历史报告目录：{exc}", error_code="REPORT_HISTORY_UNAVAILABLE")
        files = [
            {
                "name": report["filename"],
                "mode": report["mode"],
                "esp_stem": report["esp_stem"],
                "timestamp": report["timestamp"].isoformat(timespec="seconds") if report["timestamp"] else None,
                "size": report["size"],
            }
            for report in reports[:limit]
        ]
        return ToolResult.ok(f"已列出 {len(files)} 份报告" if files else "暂无历史报告", data={"files": files})


_proofreader_ctrl = ProofreaderController()


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
                "name": "get_quality_report",
                "display_name": "质量报告",
                "description": "Read the latest saved AI task quality report for this session and project version.",
                "execute": _tool_get_quality_report,
                "permission": "read",
                "parameters": {},
            },
            {
                "name": "list_quality_reports",
                "display_name": "历史报告",
                "description": "List exported Excel files in the desktop AI translation report history across plugins, "
                "newest first. Returns name, report type, plugin, time and size. Optional limit, default 50.",
                "execute": _tool_list_quality_reports,
                "permission": "read",
                "parameters": {
                    "limit": {"type": "int", "required": False, "description": "Maximum number of reports to return"}
                },
            },
        ],
    )


_register_proofreader_tools()
