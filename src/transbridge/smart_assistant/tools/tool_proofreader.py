"""P1 后处理工具 — 统一 run_postprocess 工具包装 PostProcessor 五阶段流水线 (proofreader namespace)。

Story 25: 废弃 5 个独立工具 → 1 个 run_postprocess 统一工具，与 GUI PostProcessor 行为一致。
"""
from __future__ import annotations

import threading
import logging
import time
from .base import ToolResult
from .task_manager import TaskManager

logger = logging.getLogger(__name__)

# Module-level cache for the last post-processing report result.
# Populated by _tool_run_postprocess on completion, consumed by _tool_get_quality_report.
_last_report: dict | None = None


# ── Story 25: 统一后处理工具 ───────────────────────────────────

def _tool_run_postprocess(args: dict, ctx) -> ToolResult:
    """运行完整的后处理流水线（与 GUI PostProcessor 五阶段流程一致）。

    phases 参数可选择运行的阶段，默认全部:
    ["consistency", "format", "quality_gate", "refinement", "polish", "arbitration"]
    """
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有加载翻译集合")

    phases = args.get("phases", ["consistency", "format", "quality_gate",
                                  "refinement", "polish", "arbitration"])
    entry_ids = args.get("entry_ids")

    # 从 translation_scope 解析条目范围
    if not entry_ids:
        scope = getattr(ctx, 'translation_scope', None)
        if scope and any(scope.get(k) for k in ('stages', 'labels', 'categories')):
            from .base import filter_entries
            filter_state = {
                "stage": scope.get("stages"),
                "category": scope.get("categories"),
                "labels": scope.get("labels"),
            }
            entry_labels = getattr(ctx, 'entry_labels', None)
            scoped = filter_entries(collection, filter_state, entry_labels=entry_labels)
            entry_ids = [e.key for e in scoped]

    entries = [collection.get(eid) for eid in entry_ids] if entry_ids else list(collection)
    entries = [e for e in entries if e is not None]

    if not entries:
        return ToolResult.fail("没有可处理的条目")

    # 创建 LLMClient 和 TermDatabaseManager
    from src.transbridge.paratranz.config_manager import LLMConfig
    from src.transbridge.infra.llm_client import create_llm_client
    from src.transbridge.ai_translator.term_database import TermDatabaseManager

    llm_cfg = LLMConfig.load_from_file()
    llm_client = create_llm_client(llm_cfg)

    term_mgr = TermDatabaseManager(
        config=llm_cfg,
        esp_path=getattr(ctx, 'esp_path', None) or "",
    )
    term_mgr.load_all()

    # 构建 PostProcessorConfig（从 LLMConfig 加载，与 GUI 一致）
    from src.transbridge.ai_translator.post_processor.post_processor import (
        PostProcessor, PostProcessorConfig,
    )
    config = PostProcessorConfig.from_llm_config(llm_cfg)

    # 按 phases 参数覆盖配置开关
    config.enable_consistency_check = "consistency" in phases
    config.enable_format_validation = "format" in phases
    config.enable_quality_gate = "quality_gate" in phases
    config.enable_refinement = "refinement" in phases
    config.enable_polish = "polish" in phases
    config.enable_llm_arbitration = "arbitration" in phases

    stop_event = threading.Event()
    tm = TaskManager()
    task_id = tm.register(stop_event=stop_event, metadata={"phases": phases, "type": "postprocess"})

    def _run():
        global _last_report
        try:
            processor = PostProcessor(config)
            processor.register_default_checkers(
                term_manager=term_mgr,
                llm_client=llm_client,
            )
            result = processor.process_entries(
                entries, stop_event=stop_event,
                esp_path=getattr(ctx, 'esp_path', None),
            )

            _last_report = {
                "phase": "postprocess",
                "phases": list(phases),
                "total_checked": result.total_checked,
                "issue_count": result.issue_count,
                "auto_fixed": result.auto_fixed,
                "needs_review": list(result.needs_review),
                "issues": [
                    {"entry_id": iss.entry_id, "issue_type": iss.issue_type,
                     "severity": iss.severity, "message": iss.message}
                    for iss in result.issues[:50]
                ],
                "timestamp": time.time(),
            }

            tm.update_progress(task_id, {
                "status": "completed",
                "total_checked": result.total_checked,
                "issue_count": result.issue_count,
                "auto_fixed": result.auto_fixed,
            })
            tm.set_status(task_id, "completed")
            tm.notify_completed(task_id, {
                "status": "completed",
                "total_checked": result.total_checked,
                "issue_count": result.issue_count,
                "auto_fixed": result.auto_fixed,
            })
        except Exception as exc:
            logger.exception("后处理异常: %s", exc)
            tm.set_status(task_id, "failed")
            tm.update_progress(task_id, {"error": str(exc)})
            tm.notify_failed(task_id, str(exc))

    thread = threading.Thread(target=_run, daemon=True)
    handle = tm.get_handle(task_id)
    if handle:
        handle._thread = thread
    thread.start()

    return ToolResult.ok(
        f"后处理已启动 (phases={phases}, entries={len(entries)})",
        data={"task_id": task_id, "phases": phases, "entry_count": len(entries)},
    )


def _tool_get_quality_report(args: dict, ctx) -> ToolResult:
    """获取最近的质量报告摘要。"""
    global _last_report
    if _last_report is None:
        return ToolResult.ok("暂无质量报告", data={"reports": []})

    report = _last_report
    return ToolResult.ok(
        f"最近报告: 检查{report['total_checked']}条, "
        f"发现问题{report['issue_count']}个, "
        f"自动修复{report['auto_fixed']}个",
        data={"reports": [report]},
    )


# ── 注册 ──────────────────────────────────────────────────────

def _register_proofreader_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry
    ToolRegistry.register_tools("proofreader", [
        {"name": "run_postprocess", "display_name": "后处理流水线",
         "description": "运行完整的后处理流水线（与界面后处理一致）。"
         "phases 参数选择阶段: consistency(术语一致性检查)/format(格式校验)/quality_gate(质量关卡)/refinement(LLM修复)/polish(LLM润色)/arbitration(LLM裁决)，默认全部。"
         "entry_ids 可选指定条目key列表，不传则从当前翻译作用域解析。"
         "需用户确认（产生LLM费用），后台运行，通过 get_task_status 查询进度。",
         "execute": _tool_run_postprocess, "permission": "write", "is_long_running": True,
         "require_confirmation": True},
        {"name": "get_quality_report", "display_name": "质量报告", "description": "获取最近后处理报告摘要",
         "execute": _tool_get_quality_report, "permission": "read"},
    ])


_register_proofreader_tools()
