"""P1 后处理工具 — 一致性检查/格式校验/LLM精炼/润色/裁决 (proofreader namespace)。

Story 10 v2: E10 LLM后处理 require_confirmation, E9 _run_postprocess_phase 工厂函数。
"""
from __future__ import annotations

import threading
import logging
from types import SimpleNamespace

from .base import ToolResult
from .task_manager import TaskManager

logger = logging.getLogger(__name__)


# ── E9: PostProcessor 工厂函数 ────────────────────────────────

def _run_postprocess_phase(processor_class, config_overrides: dict,
                           args: dict, ctx, phase_name: str) -> ToolResult:
    """E9: 公共后处理工厂函数，减少 LLM 后处理工具的胶水代码重复。"""
    collection = ctx.collection
    if not collection or len(collection) == 0:
        return ToolResult.fail("当前没有加载翻译集合")

    stop_event = threading.Event()
    tm = TaskManager()
    task_id = tm.register(stop_event=stop_event, metadata={"phase": phase_name})

    def _run():
        try:
            cfg = processor_class.get_default_config() if hasattr(processor_class, 'get_default_config') else SimpleNamespace()
            for k, v in config_overrides.items():
                setattr(cfg, k, v)
            processor = processor_class(cfg) if config_overrides else processor_class()
            result = processor.process(collection)
            tm.update_progress(task_id, {"status": "completed", "summary": str(result)})
            tm.set_status(task_id, "completed")
        except Exception as exc:
            logger.exception("%s异常: %s", phase_name, exc)
            tm.set_status(task_id, "failed")
            tm.update_progress(task_id, {"error": str(exc)})

    thread = threading.Thread(target=_run, daemon=True)
    handle = tm.get_handle(task_id)
    if handle:
        handle._thread = thread
    thread.start()

    return ToolResult.ok(f"{phase_name}已启动", data={"task_id": task_id})


# ── 规则检查 (read) ────────────────────────────────────────────

def _tool_run_consistency_check(args: dict, ctx) -> ToolResult:
    """执行术语一致性检查。"""
    from src.transbridge.ai_translator.post_processor.consistency_checker import ConsistencyChecker
    return _run_postprocess_phase(
        ConsistencyChecker, {}, args, ctx, "术语一致性检查"
    )


def _tool_run_format_validation(args: dict, ctx) -> ToolResult:
    """执行格式校验。"""
    from src.transbridge.ai_translator.post_processor.format_validator import FormatValidator
    return _run_postprocess_phase(
        FormatValidator, {}, args, ctx, "格式校验"
    )


# ── LLM 后处理 (write, long_running, require_confirmation) ─────

def _tool_run_llm_refinement(args: dict, ctx) -> ToolResult:
    """LLM 修复条目。E10: require_confirmation，显示预估费用。"""
    from src.transbridge.ai_translator.post_processor.llm_refiner import LLMRefiner
    return _run_postprocess_phase(
        LLMRefiner, {"enable_refinement": True, "enable_polish": False, "enable_llm_arbitration": False},
        args, ctx, "LLM修复",
    )


def _tool_run_llm_polish(args: dict, ctx) -> ToolResult:
    """LLM 润色条目。E10: require_confirmation。"""
    from src.transbridge.ai_translator.post_processor.llm_polisher import LLMPolisher
    return _run_postprocess_phase(
        LLMPolisher, {"enable_refinement": False, "enable_polish": True, "enable_llm_arbitration": False},
        args, ctx, "LLM润色",
    )


def _tool_run_llm_arbitration(args: dict, ctx) -> ToolResult:
    """LLM 裁决条目。E10: require_confirmation。"""
    from src.transbridge.ai_translator.post_processor.llm_arbiter import LLMArbiter
    return _run_postprocess_phase(
        LLMArbiter, {"enable_refinement": False, "enable_polish": False, "enable_llm_arbitration": True},
        args, ctx, "LLM裁决",
    )


def _tool_get_quality_report(args: dict, ctx) -> ToolResult:
    """获取最近的质量报告摘要。"""
    return ToolResult.ok("暂无质量报告", data={"reports": []})


# ── 注册 ──────────────────────────────────────────────────────

def _register_proofreader_tools():
    from src.transbridge.smart_assistant.tool_registry import ToolRegistry, ToolSpec

    tools = [
        ("run_consistency_check", "一致性检查", "执行术语一致性检查", _tool_run_consistency_check, "read"),
        ("run_format_validation", "格式校验", "执行翻译格式校验", _tool_run_format_validation, "read"),
        ("run_llm_refinement", "LLM修复", "LLM修复翻译问题。E10:需确认（预估费用）", _tool_run_llm_refinement, "write"),
        ("run_llm_polish", "LLM润色", "LLM润色翻译。E10:需确认（预估费用）", _tool_run_llm_polish, "write"),
        ("run_llm_arbitration", "LLM裁决", "LLM裁决翻译方案。E10:需确认（预估费用）", _tool_run_llm_arbitration, "write"),
        ("get_quality_report", "质量报告", "获取最近质量报告摘要", _tool_get_quality_report, "read"),
    ]

    for name, display_name, description, execute, permission in tools:
        is_llm = name.startswith("run_llm_")
        ToolRegistry.register(ToolSpec(
            name=name, display_name=display_name, description=description,
            parameters={}, execute=execute, permission=permission,
            is_long_running=is_llm,
            require_confirmation=is_llm,  # E10: LLM后处理需确认
        ), namespace="proofreader")


_register_proofreader_tools()
