"""P1 后处理工具 — 统一 run_postprocess 工具包装 PostProcessor 五阶段流水线 (proofreader namespace)。

Story 25: 废弃 5 个独立工具 → 1 个 run_postprocess 统一工具，与 GUI PostProcessor 行为一致。
补全: 集成 ReportGenerator 生成 Excel 报告 + 中间数据保留 + 历史报告列表。
Story 03B: 重构为 ProofreaderController 类。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .base import ToolResult
from .task_manager import TaskManager

if TYPE_CHECKING:
    from src.transbridge.ai_translator.post_processor.base import PostProcessResult

logger = logging.getLogger(__name__)

# Module-level cache for the last post-processing report result.
# Populated by run_postprocess on completion, consumed by get_quality_report.
# M8: 通过 set_last_report/get_last_report 访问，内部加锁防止跨线程竞态。
_last_report: dict | None = None
_last_report_lock = threading.Lock()


def set_last_report(report: dict) -> None:
    """M8: 线程安全写入最近报告，供 tool_translator 跨模块使用。"""
    global _last_report
    with _last_report_lock:
        _last_report = report


def get_last_report() -> dict | None:
    """M8: 线程安全读取最近报告。"""
    with _last_report_lock:
        return _last_report


# ── ProofreaderController ─────────────────────────────────────

class ProofreaderController:
    """后处理控制器：统一管理 proofreader 命名空间的工具逻辑。"""

    def __init__(self, app_context, task_manager):
        self._ctx = app_context
        self._task_mgr = task_manager

    # ── M10: _build_postprocessor helper ──────────────────────────

    def _build_postprocessor(self, entries, llm_cfg, phases, max_workers, ctx):
        """M10: 提取 PostProcessor 构建逻辑（~30行）。

        Returns:
            (processor, config, llm_client, term_mgr): 配置好的后处理器及其依赖。
        Raises:
            ValueError: 若 API Key 未配置。
        """
        from src.transbridge.infra.llm_client import create_llm_client
        from src.transbridge.ai_translator.term_database import TermDatabaseManager
        from src.transbridge.ai_translator.post_processor.post_processor import (
            PostProcessor, PostProcessorConfig,
        )

        if not llm_cfg.api_key:
            raise ValueError("API Key 未配置，请在 AI 翻译设置中配置 API Key")
        llm_client = create_llm_client(llm_cfg)

        term_mgr = TermDatabaseManager(
            config=llm_cfg,
            esp_path=getattr(ctx, 'esp_path', None) or "",
        )
        term_mgr.load_all()

        config = PostProcessorConfig.from_llm_config(llm_cfg)
        config.enable_consistency_check = "consistency" in phases
        config.enable_format_validation = "format" in phases
        config.enable_quality_gate = "quality_gate" in phases
        config.enable_refinement = "refinement" in phases
        config.enable_polish = "polish" in phases
        config.enable_llm_arbitration = "arbitration" in phases

        processor = PostProcessor(config)
        processor.register_default_checkers(
            term_manager=term_mgr,
            llm_client=llm_client,
        )
        return processor, config, llm_client, term_mgr

    # ── Story 25: 统一后处理工具 ───────────────────────────────────

    def run_postprocess(self, args: dict, ctx) -> ToolResult:
        """运行完整的后处理流水线（与 GUI PostProcessor 五阶段流程一致）。

        phases 参数可选择运行的阶段，默认全部:
        ["consistency", "format", "quality_gate", "refinement", "polish", "arbitration"]
        """
        collection = ctx.collection
        if not collection or len(collection) == 0:
            return ToolResult.fail("当前没有加载翻译集合")

        phases = args.get("phases", ["consistency", "format", "quality_gate",
                                      "refinement", "polish", "arbitration"])
        valid_phases = {"consistency", "format", "quality_gate", "refinement", "polish", "arbitration"}
        invalid = [p for p in phases if p not in valid_phases]
        if invalid:
            return ToolResult.fail(f"无效的阶段名: {invalid}，可选: {sorted(valid_phases)}")
        max_workers = args.get("max_workers", 1)
        if not isinstance(max_workers, int) or max_workers < 1:
            max_workers = 1
        elif max_workers > 8:
            max_workers = 8
        entry_ids = args.get("entry_ids")

        # M3: 复用 resolve_scope_to_entry_ids 消除与 tool_translator 的重复代码
        if not entry_ids:
            from .base import resolve_scope_to_entry_ids
            entry_ids = resolve_scope_to_entry_ids(ctx, collection)

        entries = [collection.get(eid) for eid in entry_ids] if entry_ids is not None else list(collection)
        entries = [e for e in entries if e is not None]

        if entry_ids and not entries:
            return ToolResult.fail("所有指定的 entry_id 均无效，未找到匹配条目")
        if not entries:
            return ToolResult.fail("没有可处理的条目")

        stop_event = threading.Event()
        pause_event = threading.Event()
        pause_event.set()  # 初始非暂停状态
        tm = TaskManager()
        task_id = tm.register(stop_event=stop_event, metadata={"phases": phases, "type": "postprocess"})
        # 将 pause_event 回写到 TaskHandle，使 stop_task 可访问
        handle = tm.get_handle(task_id)
        if handle:
            handle.pause_event = pause_event

        def _run():
            cp = None
            last_phase = [None]  # 列表以在闭包中修改
            all_entry_keys = [e.key for e in entries]

            try:
                # ── 加载 checkpoint ───────────────────────────────────────
                esp_path = getattr(ctx, 'esp_path', None)
                if esp_path:
                    try:
                        from src.transbridge.ai_translator.post_processor.checkpoint import PostProcessCheckpoint
                        cp = PostProcessCheckpoint.load(esp_path)
                        if cp:
                            logger.info("从 checkpoint 恢复: %s, 已完成阶段=%s",
                                        esp_path, list(cp.completed_batches.keys()))
                    except Exception:
                        logger.warning("Checkpoint 加载失败，从头开始")
                        cp = None

                # M10: 提取 PostProcessor 构建逻辑
                from ._common import load_llm_config
                llm_cfg = load_llm_config()
                processor, config, llm_client, term_mgr = self._build_postprocessor(
                    entries, llm_cfg, phases, max_workers, ctx)

                # 若从 checkpoint 恢复且已有 checkpoint，传递给 processor
                if cp is None and esp_path:
                    from src.transbridge.ai_translator.post_processor.checkpoint import PostProcessCheckpoint
                    cp = PostProcessCheckpoint(esp_stem=Path(esp_path).stem)
                # 进度回调 → TaskManager + checkpoint 阶段保存
                def _progress(phase, current, total, message):
                    tm.update_progress(task_id, {
                        "phase": phase, "current": current,
                        "total": total, "message": message,
                    })
                    # 阶段切换时保存 checkpoint
                    if phase != last_phase[0] and last_phase[0] is not None:
                        if cp and esp_path:
                            cp.mark_batch_completed(last_phase[0], all_entry_keys)
                            cp.save(esp_path)
                    last_phase[0] = phase

                result = processor.process_entries(
                    entries, stop_event=stop_event,
                    pause_event=pause_event,
                    checkpoint=cp,
                    esp_path=getattr(ctx, 'esp_path', None),
                    progress_callback=_progress,
                    max_workers=max_workers,
                )

                # 正常完成 → 删除 checkpoint
                if cp and esp_path:
                    cp.delete(esp_path)

                # ── 构建 report ──────────────────────────────────────────
                verdict_stats = {"passed": 0, "rejected": 0, "pending": 0}
                exec_result = getattr(result, "execution_result", None)
                if exec_result:
                    verdict_stats = {
                        "passed": getattr(exec_result, "passed", 0),
                        "rejected": getattr(exec_result, "rejected", 0),
                        "pending": getattr(exec_result, "pending", 0),
                    }

                # 中间数据摘要
                refine_summaries = self._summarize_refine_results(result.refine_results)
                polish_summaries = self._summarize_polish_results(result.polish_results)
                decision_summaries = self._summarize_decisions(result.decisions)

                # ── 生成 Excel 报告 ───────────────────────────────────────
                report_file = self._generate_report(
                    entries, result, getattr(ctx, 'esp_path', None), llm_cfg,
                )

                set_last_report({
                    "phase": "postprocess",
                    "phases": list(phases),
                    "total_checked": result.total_checked,
                    "issue_count": result.issue_count,
                    "auto_fixed": result.auto_fixed,
                    "needs_review": list(result.needs_review),
                    "verdict_stats": verdict_stats,
                    "issues": [
                        {"entry_id": iss.entry_id, "issue_type": iss.issue_type,
                         "severity": iss.severity, "message": iss.message,
                         "original": iss.original, "translation": iss.translation,
                         "suggestion": getattr(iss, 'suggestion', '')}
                        for iss in result.issues[:50]
                    ],
                    "refine_results": refine_summaries,
                    "polish_results": polish_summaries,
                    "decisions": decision_summaries,
                    "report_file": report_file,
                    "timestamp": time.time(),
                })

                completion_data = {
                    "status": "completed",
                    "total_checked": result.total_checked,
                    "issue_count": result.issue_count,
                    "auto_fixed": result.auto_fixed,
                    "verdict_stats": verdict_stats,
                }
                if report_file:
                    completion_data["report_file"] = report_file

                tm.update_progress(task_id, completion_data)
                tm.set_status(task_id, "completed")
                tm.notify_completed(task_id, completion_data)
                # G3: 通知 UI 集合已修改，Step2 表格自动刷新
                ctx.safe_mutate(lambda: ctx.notify_collection_modified())
            except Exception as exc:
                logger.exception("后处理异常: %s", exc)
                tm.set_status(task_id, "failed")
                tm.update_progress(task_id, {"error": str(exc)})
                tm.notify_failed(task_id, str(exc))
                # 异常中断 → 保留 checkpoint 以便恢复（不删除）

        thread = tm.start_thread(task_id, _run)  # M2: 复用 TaskManager.start_thread
        # m6: 无全局线程池，每次后处理创建独立 Thread。并发上限由 max_workers 和 TaskManager 控制。

        return ToolResult.ok(
            f"后处理已启动 (phases={phases}, entries={len(entries)})",
            data={"task_id": task_id, "phases": phases, "entry_count": len(entries)},
        )

    def get_quality_report(self, args: dict, ctx) -> ToolResult:
        """获取最近的质量报告摘要（含中间数据和报告文件路径）。"""
        report = get_last_report()
        if report is None:
            return ToolResult.ok("暂无质量报告", data={"reports": []})

        phase = report.get("phase", "postprocess")
        if phase == "polish":
            lines = [f"最近润色报告: 条目{report.get('entry_count', '?')}条, "
                     f"润色级别{report.get('polish_level', '?')}, "
                     f"范围{report.get('scope', '?')}, "
                     f"变更总计{report.get('total', '?')}处"]
        else:
            lines = [f"最近报告: 检查{report.get('total_checked', '?')}条, "
                     f"发现问题{report.get('issue_count', '?')}个, "
                     f"自动修复{report.get('auto_fixed', '?')}个"]
            vs = report.get("verdict_stats", {})
            if vs:
                lines.append(f"裁决: 通过{vs.get('passed', 0)}/打回{vs.get('rejected', 0)}/待审{vs.get('pending', 0)}")
        if report.get("report_file"):
            lines.append(f"报告文件: {report['report_file']}")
        return ToolResult.ok(" | ".join(lines), data={"reports": [report]})

    def list_quality_reports(self, args: dict, ctx) -> ToolResult:
        """列出历史后处理报告文件（Excel .xlsx）。

        扫描 data/ai_translator/{esp_stem}/reports/ 目录，
        返回文件名、大小、修改时间，按时间倒序排列。
        """
        esp_path = getattr(ctx, 'esp_path', None)
        if not esp_path:
            return ToolResult.ok("未加载 ESP，无法定位报告目录", data={"files": []})

        from src.transbridge.paratranz.config_manager import LLMConfig
        ai_dir = LLMConfig.get_ai_translator_dir(Path(esp_path).stem)
        reports_dir = os.path.join(ai_dir, "reports")

        limit = args.get("limit", 50)
        files = []
        try:
            if os.path.isdir(reports_dir):
                entries = sorted(
                    [p for p in Path(reports_dir).iterdir() if p.suffix == ".xlsx"],
                    key=lambda p: p.stat().st_mtime, reverse=True,
                )
                for p in entries[:limit]:
                    st = p.stat()
                    files.append({
                        "name": p.name,
                        "size": st.st_size,
                        "modified_at": st.st_mtime,
                    })
        except OSError as exc:
            logger.warning("扫描报告目录失败: %s", exc)

        return ToolResult.ok(
            f"共 {len(files)} 份报告" if files else "暂无历史报告",
            data={"files": files},
        )

    # ── 辅助函数 ────────────────────────────────────────────────────

    def _summarize_refine_results(self, refine_results: dict | None) -> list[dict]:
        """将 RefineResult 字典转为可序列化的摘要列表。"""
        if not refine_results:
            return []
        summaries = []
        for entry_id, ref in refine_results.items():
            summaries.append({
                "entry_id": entry_id,
                "refined_translation": getattr(ref, 'refined_translation', '') or '',
                "confidence": getattr(ref, 'confidence', 0.0),
            })
        return summaries

    def _summarize_polish_results(self, polish_results: dict | None) -> list[dict]:
        """将 PolishResult 字典转为可序列化的摘要列表。"""
        if not polish_results:
            return []
        summaries = []
        for entry_id, pol in polish_results.items():
            changes = getattr(pol, 'changes', None) or []
            summaries.append({
                "entry_id": entry_id,
                "polished_translation": getattr(pol, 'polished_translation', '') or '',
                "confidence": getattr(pol, 'confidence', 0.0),
                "changes_count": len(changes),
            })
        return summaries

    def _summarize_decisions(self, decisions: dict | None) -> list[dict]:
        """将 ArbiterDecision 字典转为可序列化的摘要列表。"""
        if not decisions:
            return []
        summaries = []
        for entry_id, dec in decisions.items():
            summaries.append({
                "entry_id": entry_id,
                "verdict": getattr(dec, 'verdict', '') or '',
                "reason": getattr(dec, 'reason', '') or '',
                "suggested_action": getattr(dec, 'suggested_action', '') or '',
                "confidence": getattr(dec, 'confidence', 0.0),
            })
        return summaries

    def _generate_report(
        self,
        entries: list,
        pp_result,
        esp_path: str | None,
        llm_cfg,
    ) -> str | None:
        """调用 ReportGenerator 生成 Excel 报告，返回文件路径。"""
        if not esp_path or not pp_result:
            return None

        try:
            from types import SimpleNamespace
            from src.transbridge.ai_translator.post_processor.report_generator import ReportGenerator

            esp_stem = Path(esp_path).stem

            fake_result = SimpleNamespace(
                success_count=len(entries),
                failed_count=0,
                skipped_count=0,
                new_dynamic_terms=0,
                post_process_result=pp_result,
            )

            generator = ReportGenerator(esp_stem)
            report_path = generator.generate_translate_report(
                result=fake_result,
                refine_results=pp_result.refine_results,
                polish_results=pp_result.polish_results,
                decisions=pp_result.decisions,
            )
            return report_path
        except Exception:
            logger.exception("后处理报告生成失败")
            return None


# ── 惰性初始化 + 模块级 wrapper ──────────────────────────────────

_proofreader_ctrl: ProofreaderController | None = None


def _get_proofreader_controller() -> ProofreaderController:
    global _proofreader_ctrl
    if _proofreader_ctrl is None:
        from src.transbridge.ui.context import AppContext
        from .task_manager import TaskManager
        _proofreader_ctrl = ProofreaderController(AppContext(), TaskManager())
    return _proofreader_ctrl


def _tool_run_postprocess(args: dict, ctx) -> ToolResult:
    return _get_proofreader_controller().run_postprocess(args, ctx)


def _tool_get_quality_report(args: dict, ctx) -> ToolResult:
    return _get_proofreader_controller().get_quality_report(args, ctx)


def _tool_list_quality_reports(args: dict, ctx) -> ToolResult:
    return _get_proofreader_controller().list_quality_reports(args, ctx)


# ── 注册 ──────────────────────────────────────────────────────

def _register_proofreader_tools():
    from ..tool_registry import ToolRegistry
    ToolRegistry.register_tools("proofreader", [
        {"name": "run_postprocess", "display_name": "后处理流水线",
         "description": "①运行六阶段后处理流水线：consistency(术语一致性检查)/format(格式校验)/quality_gate(质量关卡)/refinement(LLM修复)/polish(LLM润色)/arbitration(LLM裁决)，按固定顺序执行。②参数: phases(可选，默认全部阶段)/entry_ids(可选，不传则从set_scope设置的翻译作用域解析)/max_workers(可选，1-8，默认1)。③返回{task_id, phases, entry_count}。规则: 后台运行，需用户确认(产生LLM费用)，通过get_task_status查询进度，支持stop_task pause/resume暂停恢复，每阶段完成后自动保存断点可续传。完成后用get_quality_report查看报告摘要，用list_quality_reports查看历史Excel文件。",
         "execute": _tool_run_postprocess, "permission": "write", "is_long_running": True,
         "require_confirmation": True,
         "parameters": {"phases": {"type": "list", "required": False, "description": "运行阶段列表"},
                        "entry_ids": {"type": "list", "required": False, "description": "条目key列表"},
                        "max_workers": {"type": "int", "required": False, "description": "并发线程数(1-8)"}}},
        {"name": "get_quality_report", "display_name": "质量报告",
         "description": "①获取最近一次run_postprocess的完整质量报告摘要，覆盖全部已完成阶段的聚合结果。②无参数。③返回{reports: [{phase, total_checked, issue_count, auto_fixed, needs_review, issues[], refine_results, polish_results, decisions, report_file, timestamp}]}，无报告时返回{reports: []}。规则: 只读，reports为单元素数组(非每阶段一条)，若最近一次为start_polish触发的polish-only则字段结构不同(含entry_count/polish_level/scope)。历史报告文件列表请用list_quality_reports。",
         "execute": _tool_get_quality_report, "permission": "read",
         "parameters": {}},
        {"name": "list_quality_reports", "display_name": "历史报告",
         "description": "①列出历史后处理Excel报告文件，按修改时间降序排列。②参数: limit(可选，默认50)。③返回{files: [{name, size, modified_at}], directory}。规则: 只读，仅返回文件元数据(文件名/大小/修改时间)，LLM无法读取Excel内容。查看最新报告摘要请用get_quality_report。",
         "execute": _tool_list_quality_reports, "permission": "read",
         "parameters": {"limit": {"type": "int", "required": False, "description": "返回数量上限，默认50"}}},
    ])


_register_proofreader_tools()
