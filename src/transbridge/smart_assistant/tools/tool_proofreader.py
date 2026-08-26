"""P1 后处理工具 — 统一 run_postprocess 工具包装 PostProcessor 五阶段流水线 (proofreader namespace)。

Story 25: 废弃 5 个独立工具 → 1 个 run_postprocess 统一工具，与 GUI PostProcessor 行为一致。
补全: 集成统一 ReportSnapshot 报表 bundle + 中间数据保留 + 历史报告列表。
Story 03B: 重构为 ProofreaderController 类。
"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
import time

from .base import ToolResult, require_runtime_context
from .task_manager import TaskManager

logger = logging.getLogger(__name__)

# Module-level cache for the last post-processing report result.
# Populated by run_postprocess on completion, consumed by get_quality_report.
# M8: 通过 set_last_report/get_last_report 访问，内部加锁防止跨线程竞态。
_last_report: dict | None = None
_last_report_lock = threading.Lock()


def _count_committed_fixes(candidates) -> int:
    """Count changed candidates that were actually accepted for commit."""
    return sum(1 for candidate in candidates if candidate.accepted and candidate.text != candidate.before_text)


def set_last_report(report: dict) -> None:
    """M8: 线程安全写入最近报告，供 tool_translator 跨模块使用。"""
    global _last_report
    with _last_report_lock:
        _last_report = report


def get_last_report() -> dict | None:
    """M8: 线程安全读取最近报告。"""
    with _last_report_lock:
        return _last_report


def _resolve_report_directory(ctx) -> Path:
    """Return the canonical report directory, with a safe project-less fallback."""
    from transbridge.paratranz.config_manager import LLMConfig, ParatranzConfig

    esp_path = getattr(ctx, "esp_path", None)
    if esp_path:
        return Path(LLMConfig.get_ai_translator_dir(Path(esp_path).stem)) / "reports"
    return Path(ParatranzConfig.get_data_dir()) / "reports" / "postprocess"


# ── ProofreaderController ─────────────────────────────────────


class ProofreaderController:
    """后处理控制器：统一管理 proofreader 命名空间的工具逻辑。"""

    def __init__(self, app_context=None, task_manager=None):
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
        from transbridge.ai_translator.post_processor.post_processor import (
            PostProcessor,
            PostProcessorConfig,
        )
        from transbridge.ai_translator.term_database import TermDatabaseManager
        from transbridge.infra.llm_client import create_llm_client

        if not llm_cfg.api_key:
            raise ValueError("API Key 未配置，请在 AI 翻译设置中配置 API Key")
        llm_client = create_llm_client(llm_cfg)

        term_mgr = TermDatabaseManager(
            config=llm_cfg,
            esp_path=getattr(ctx, "esp_path", None) or "",
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

        phases = args.get("phases", ["consistency", "format", "quality_gate", "refinement", "polish", "arbitration"])
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
            try:
                from transbridge.application.contracts import OperationOutcome, RequestContext
                from transbridge.application.io import StagePolicy
                from transbridge.application.io.publish import ImmediateCommitGuard
                from transbridge.application.translation import (
                    CheckerStage,
                    FilesystemPostProcessCheckpointPort,
                    FilesystemTranslationCheckpointPort,
                    LlmPostProcessStage,
                    OpenAiPostProcessHttpPort,
                    PostProcessExecutionService,
                    PostProcessLlmPhase,
                    PostProcessWorkload,
                    TranslationInput,
                    render_report_bundle,
                )
                from transbridge.paratranz.config_manager import ParatranzConfig

                from ._common import load_llm_config

                llm_cfg = load_llm_config()
                processor, _config, _llm_client, _term_mgr = self._build_postprocessor(
                    entries, llm_cfg, phases, max_workers, ctx
                )
                stages = []
                stage_names = []
                checker_phases = {
                    "ConsistencyChecker": "consistency",
                    "FormatValidator": "format",
                    "QualityGateChecker": "quality_gate",
                }
                for checker in processor._checkers:
                    phase = checker_phases.get(type(checker).__name__)
                    if phase in phases:
                        stages.append(CheckerStage(phase, checker))
                        stage_names.append(phase)
                llm_port = OpenAiPostProcessHttpPort(credential=lambda: llm_cfg.api_key)
                llm_phases = {
                    "refinement": PostProcessLlmPhase.REFINE,
                    "polish": PostProcessLlmPhase.POLISH,
                    "arbitration": PostProcessLlmPhase.ARBITRATE,
                }
                for requested_phase, phase in llm_phases.items():
                    if requested_phase in phases:
                        stages.append(
                            LlmPostProcessStage(
                                phase,
                                llm_port,
                                target_locale=llm_cfg.target_lang,
                                game_profile=llm_cfg.game_profile,
                                base_url=llm_cfg.base_url,
                                model=llm_cfg.model,
                            )
                        )
                        stage_names.append(requested_phase)

                checkpoint_root = Path(ParatranzConfig.get_data_dir()) / "checkpoints"
                workload = PostProcessWorkload(
                    tuple(stages),
                    stage_policy=StagePolicy(),
                    stage_names=tuple(stage_names),
                    checkpoint_port=FilesystemPostProcessCheckpointPort(checkpoint_root / "postprocess"),
                )
                request_context = getattr(ctx, "request_context", None)
                owner_id = getattr(request_context, "owner_id", "") or getattr(ctx, "owner_id", "")
                context = RequestContext(
                    owner_id or "smart-assistant",
                    run_id=task_id,
                    project_id=getattr(request_context, "project_id", None),
                    variant_id=getattr(request_context, "variant_id", None),
                    session_id=getattr(request_context, "session_id", None),
                    permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
                )
                inputs = tuple(
                    TranslationInput(
                        entry.identity,
                        entry.revision,
                        entry.original,
                        entry.translation,
                        entry.stage,
                        entry.context or "",
                    )
                    for entry in entries
                )
                execution = PostProcessExecutionService(workload).execute(
                    run_id=task_id,
                    entries=inputs,
                    collection=collection,
                    context=context,
                    commit_guard=ImmediateCommitGuard(task_id, active=lambda: not stop_event.is_set()),
                    commit_checkpoint=FilesystemTranslationCheckpointPort(checkpoint_root / "translation"),
                    is_cancelled=stop_event.is_set,
                    run_spec_summary={"phases": list(phases), "model": llm_cfg.model},
                )
                report = execution.report_snapshot
                if report is None:  # compatibility guard for third-party execution adapters
                    raise RuntimeError("后处理未生成终态报告快照")
                rendered = render_report_bundle(
                    report,
                    base_dir=_resolve_report_directory(ctx),
                )
                artifacts = rendered.value.artifacts if rendered.value is not None else ()
                artifact_paths = [artifact.artifact_path for artifact in artifacts if artifact.artifact_path]
                excel_path = next(
                    (
                        artifact.artifact_path
                        for artifact in artifacts
                        if artifact.renderer == "excel" and artifact.artifact_path
                    ),
                    None,
                )
                render_diagnostics = [diagnostic.to_dict() for diagnostic in rendered.diagnostics]
                report_data = {
                    "phase": "postprocess",
                    "phases": list(phases),
                    "total_checked": report.input_count,
                    "issue_count": report.issue_count,
                    "auto_fixed": (
                        _count_committed_fixes(report.candidates) if execution.report_result.value is not None else 0
                    ),
                    "needs_review": [
                        candidate.entry_key.serialize() for candidate in report.candidates if not candidate.accepted
                    ],
                    "verdict_stats": {
                        "passed": report.accepted_count,
                        "rejected": len(report.candidates) - report.accepted_count,
                        "pending": 0,
                    },
                    "issues": [diagnostic.to_dict() for diagnostic in report.diagnostics[:50]],
                    "report_file": excel_path or (artifact_paths[0] if artifact_paths else None),
                    "report_files": artifact_paths,
                    "report_diagnostics": render_diagnostics,
                    "report_fingerprint": report.fingerprint,
                    "outcome": execution.outcome.value,
                    "timestamp": time.time(),
                }
                set_last_report(report_data)

                completion_data = {
                    key: report_data[key]
                    for key in ("total_checked", "issue_count", "auto_fixed", "verdict_stats", "outcome")
                }
                completion_data["report_file"] = report_data["report_file"]
                completion_data["report_files"] = report_data["report_files"]
                completion_data["report_diagnostics"] = report_data["report_diagnostics"]

                if execution.report_result.outcome is OperationOutcome.CANCELLED:
                    tm.update_progress(task_id, completion_data)
                    tm.set_status(task_id, "cancelled")
                    tm.notify_failed(task_id, "任务已被用户停止；终态报告已保存")
                    return
                if execution.report_result.value is None:
                    codes = ", ".join(item.code for item in execution.report_result.diagnostics)
                    raise RuntimeError(f"后处理候选阶段失败: {codes or 'POSTPROCESS_FAILED'}")
                if execution.commit_result is not None and execution.commit_result.outcome not in {
                    OperationOutcome.COMPLETED,
                    OperationOutcome.PARTIAL,
                }:
                    codes = ", ".join(item.code for item in execution.commit_result.diagnostics)
                    raise RuntimeError(f"后处理提交失败: {codes or 'POSTPROCESS_COMMIT_FAILED'}")

                tm.update_progress(
                    task_id,
                    {
                        "outcome": completion_data["outcome"],
                        "current": completion_data["total_checked"],
                        "total": completion_data["total_checked"],
                        "issue_count": completion_data["issue_count"],
                        "auto_fixed": completion_data["auto_fixed"],
                    },
                )
                tm.set_status(task_id, "completed")
                tm.notify_completed(task_id, completion_data)
                if execution.commit_result is not None:
                    ctx.safe_mutate(lambda: ctx.notify_collection_modified())
            except Exception as exc:
                logger.exception("后处理异常: %s", exc)
                tm.set_status(task_id, "failed")
                tm.update_progress(task_id, {"error": str(exc)})
                tm.notify_failed(task_id, str(exc))

        tm.start_thread(task_id, _run)  # M2: 复用 TaskManager.start_thread
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
            lines = [
                f"最近润色报告: 条目{report.get('entry_count', '?')}条, "
                f"润色级别{report.get('polish_level', '?')}, "
                f"范围{report.get('scope', '?')}, "
                f"变更总计{report.get('total', '?')}处"
            ]
        else:
            lines = [
                f"最近报告: 检查{report.get('total_checked', '?')}条, "
                f"发现问题{report.get('issue_count', '?')}个, "
                f"自动修复{report.get('auto_fixed', '?')}个"
            ]
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
        esp_path = getattr(ctx, "esp_path", None)
        if not esp_path:
            return ToolResult.ok("未加载 ESP，无法定位报告目录", data={"files": []})

        reports_dir = _resolve_report_directory(ctx)

        limit = args.get("limit", 50)
        files = []
        try:
            if reports_dir.is_dir():
                entries = sorted(
                    [p for p in reports_dir.iterdir() if p.suffix == ".xlsx"],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
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


# ── 无状态 controller + 模块级兼容 wrapper ───────────────────────

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


# ── 注册 ──────────────────────────────────────────────────────


def _register_proofreader_tools():
    from ..tool_registry import ToolRegistry

    ToolRegistry.register_tools(
        "proofreader",
        [
            {
                "name": "run_postprocess",
                "display_name": "后处理流水线",
                "description": (
                    "①运行六阶段后处理流水线：consistency(术语一致性检查)/format(格式校验)/"
                    "quality_gate(质量关卡)/refinement(LLM修复)/polish(LLM润色)/arbitration(LLM裁决)，"
                    "按固定顺序执行。②参数: phases(可选，默认全部阶段)/entry_ids(可选，不传则从set_scope"
                    "设置的翻译作用域解析)/max_workers(可选，1-8，默认1)。③返回{task_id, phases, entry_count}。"
                    "规则: 后台运行，需用户确认(产生LLM费用)，通过get_task_status查询进度，支持stop_task "
                    "pause/resume暂停恢复，每阶段完成后自动保存断点可续传。完成后用get_quality_report查看报告摘要，"
                    "用list_quality_reports查看历史Excel文件。"
                ),
                "execute": _tool_run_postprocess,
                "permission": "write",
                "is_long_running": True,
                "require_confirmation": True,
                "parameters": {
                    "phases": {"type": "list", "required": False, "description": "运行阶段列表"},
                    "entry_ids": {"type": "list", "required": False, "description": "条目key列表"},
                    "max_workers": {"type": "int", "required": False, "description": "并发线程数(1-8)"},
                },
            },
            {
                "name": "get_quality_report",
                "display_name": "质量报告",
                "description": (
                    "①获取最近一次run_postprocess的完整质量报告摘要，覆盖全部已完成阶段的聚合结果。"
                    "②无参数。③返回{reports: [{phase, total_checked, issue_count, auto_fixed, needs_review, "
                    "issues[], report_file, report_files[], report_diagnostics[], timestamp}]}，无报告时返回"
                    "{reports: []}。规则: 只读，reports为单元素数组(非每阶段一条)，若最近一次为"
                    "start_polish触发的polish-only则字段结构不同(含entry_count/polish_level/scope)。"
                    "历史报告文件列表请用list_quality_reports。"
                ),
                "execute": _tool_get_quality_report,
                "permission": "read",
                "parameters": {},
            },
            {
                "name": "list_quality_reports",
                "display_name": "历史报告",
                "description": (
                    "①列出历史后处理Excel报告文件，兼容旧文件名与统一ReportSnapshot报表，"
                    "按修改时间降序排列。②参数: limit(可选，默认50)。③返回{files: [{name, size, modified_at}]}。"
                    "规则: 只读，仅返回文件元数据(文件名/大小/修改时间)，LLM无法读取Excel内容。"
                    "查看最新报告摘要请用get_quality_report。"
                ),
                "execute": _tool_list_quality_reports,
                "permission": "read",
                "parameters": {"limit": {"type": "int", "required": False, "description": "返回数量上限，默认50"}},
            },
        ],
    )


_register_proofreader_tools()
