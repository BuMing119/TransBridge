# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Present extraction results and progress without hiding provider failures."""

from transbridge.application.terminology import BuildResult
from transbridge.application.terminology.workbench_status import BuildStatus

from .view_models import TechnicalDetail, TerminologySummaryViewState, phase_label


def project_build(result: BuildResult) -> TerminologySummaryViewState:
    summary = result.summary
    llm_status = str(result.llm_status)
    llm_incomplete = llm_status in {"partial", "unavailable"}
    partial = result.completeness.value == "partial" or llm_incomplete
    stale = result.freshness.value == "stale"
    if isinstance(result, BuildStatus):
        direct, extracted = result.direct_count, result.extracted_count
    else:
        direct = sum(str(item.extraction_method) == "deterministic_name" for item in result.candidates)
        extracted = sum(str(item.extraction_method) == "llm_text" for item in result.candidates)
    failed_batches = sum(item.startswith("LLM_BATCH_FAILED") for item in result.diagnostics)
    filtered = sum(item.startswith(("LLM_PROPOSAL_", "LLM_EVIDENCE_UNKNOWN")) for item in result.diagnostics)
    llm_label = {
        "performed": "LLM 抽取完成",
        "partial": "LLM 抽取部分失败",
        "unavailable": "LLM 不可用，未执行抽取",
        "skipped": "LLM 已关闭" if "LLM_DISABLED" in result.diagnostics else "没有需要 LLM 抽取的文本",
    }.get(llm_status, "LLM 执行状态待确认")
    decisions = (
        f"发现 {summary.conflict_count} 组同名异译需要决定。"
        if summary.conflict_count
        else "没有发现需要决定的同名异译。"
    )
    judgment = _judgment_summary(result.diagnostics)
    if judgment:
        decisions = f"{judgment} {decisions}"
    if stale:
        impact = "工程状态已变化，本次结果只能查看，不能发布。"
        next_action = "按当前工程状态重新构建。"
    elif llm_incomplete:
        impact = "LLM 未完整完成；已完成结果保留在草稿，已有正式版本保持不变。"
        next_action = (
            _failure_recovery(result.diagnostics)
            + "重新提取可重试未完成批次；成功批次可复用，强制重新抽取会再次请求模型。"
        )
    elif partial:
        impact = "部分来源未完成；已有正式版本和历史保持不变。"
        next_action = "检查未完成来源，或先导出质量报告。"
    else:
        impact = "应用后，后续翻译将采用本次术语结果。"
        next_action = "无差异术语已默认采用；处理冲突后即可应用，也可以丢弃本次结果。"
    return TerminologySummaryViewState(
        title="本次提取结果",
        result=(
            f"从 {summary.source_count} 个来源整理出 {summary.candidate_count} 个术语候选。"
            f"规则收集 {direct} 个，LLM 抽取 {extracted} 个。{llm_label}。"
            f"失败批次 {failed_batches}，过滤候选 {filtered}。"
        ),
        decisions=decisions,
        impact=impact,
        next_action=next_action,
        is_partial=partial,
        is_stale=stale,
        technical_details=(
            TechnicalDetail("构建引用", result.ref.build_key),
            TechnicalDetail("内容摘要", result.ref.content_digest),
            TechnicalDetail("诊断代码", "\n".join(result.diagnostics) or "无"),
        ),
        term_count=summary.candidate_count,
        attention_count=summary.conflict_count,
        llm_status=llm_status,
    )


def _judgment_summary(diagnostics):
    counts = {}
    for diagnostic in diagnostics:
        name, separator, count = diagnostic.rpartition(":")
        if separator and count.isdecimal() and name.startswith("CONFLICT_JUDGMENT_"):
            counts[name] = int(count)
    if "CONFLICT_JUDGMENT_COMPLETED" in counts:
        return (
            f"LLM 已判断 {counts['CONFLICT_JUDGMENT_COMPLETED']} 组译名差异，"
            f"{counts.get('CONFLICT_JUDGMENT_EQUIVALENT', 0)} 组为同一术语的正常表达差异，"
            f"{counts.get('CONFLICT_JUDGMENT_UNCERTAIN', 0)} 组无法确定，保留人工处理。"
            f"实际模型请求 {counts.get('CONFLICT_JUDGMENT_REQUESTS', 0)} 次；"
            f"未执行 {counts.get('CONFLICT_JUDGMENT_NOT_RUN', 0)} 组，"
            f"失败 {counts.get('CONFLICT_JUDGMENT_FAILED', 0)} 组。"
        )
    if "CONFLICT_JUDGMENT_DISABLED" in diagnostics:
        return "本次未执行 LLM 冲突判断。"
    return ""


def task_detail(state) -> str:
    detail = phase_label(state.phase)
    if state.current_object:
        detail += f" · {state.current_object}"
    if state.llm_submitted or state.llm_completed or state.llm_waiting:
        detail += f" · LLM 批次已提交 {state.llm_submitted}，已完成 {state.llm_completed}，等待 {state.llm_waiting}"
        if state.llm_retries:
            detail += f"，重试 {state.llm_retries}"
        detail += f"，耗时 {state.llm_elapsed_ms / 1000:.1f} 秒"
    return detail


def _failure_recovery(diagnostics) -> str:
    failures = " ".join(item.casefold() for item in diagnostics if item.startswith("LLM_BATCH_FAILED"))
    recovery = []
    for names, message in (
        (("timeout", "connection"), "检查网络连接与 AI 服务地址。"),
        (("authentication", "permission"), "检查 API 密钥、账户权限及模型访问权限。"),
        (("ratelimit",), "降低 AI 请求并发，等待服务限流恢复。"),
        (("structuredoutput",), "检查所选模型是否支持要求的结构化输出，必要时更换模型。"),
    ):
        if any(name in failures for name in names):
            recovery.append(message)
    return "".join(recovery) or "查看技术详情中的失败原因，并检查 AI 服务配置。"


def build_completion_message(summary) -> str:
    if summary is None or summary.is_stale:
        return "本次提取结果未确认或已过期，请重新检查项目后提取。"
    if getattr(summary, "llm_status", "") in {"partial", "unavailable"}:
        return "LLM 抽取未完整完成。已完成结果保留在草稿，请查看提取详情并重试失败部分。"
    if summary.is_partial:
        return "来源整理未完整完成，请查看来源详情并重新整理。"
    return "提取完成。无差异项已默认采用，处理剩余冲突后即可应用结果。"
