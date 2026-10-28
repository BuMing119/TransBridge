"""Per-entry task counts and actionable failure summaries."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


def failure_reason(reason: str) -> str:
    """Choose one actionable category per unsuccessful entry, never per diagnostic."""
    if any(value in reason for value in ("未返回译文", "未生成有效译文", "empty_translation", "missing_translation")):
        return "未返回译文"
    if "TOKEN_LIMIT" in reason or "Token 上限" in reason:
        return "内容超过请求上限"
    if "SCHEMA_INVALID" in reason or "格式不符合" in reason or "parse" in reason.casefold():
        return "响应格式异常"
    if "SYNTAX_MISMATCH" in reason or "占位符" in reason:
        return "标记不一致"
    if "术语" in reason and ("已变化" in reason or "被其他任务修改" in reason):
        return "术语库已变化"
    if "名称译文未遵守已有术语" in reason:
        return "名称术语不一致"
    if "TERMINOLOGY" in reason or "术语" in reason:
        return "术语修复未完成"
    if "timeout" in reason.casefold() or "超时" in reason:
        return "模型请求超时"
    if any(value in reason.casefold() for value in ("llm_call", "provider", "request", "模型调用", "请求失败")):
        return "模型请求失败"
    return "未生成有效结果"


@dataclass(frozen=True)
class TaskSummary:
    succeeded: int
    review: int
    failed: int
    unprocessed: int
    cancelled: int
    applied: int
    rejected: int
    unapplied: int
    reasons: tuple[tuple[str, int], ...]

    @property
    def counts_text(self) -> str:
        success = f"成功 {self.succeeded} 条"
        if self.review:
            success += f"（其中有疑问 {self.review} 条）"
        text = f"{success} · 未完成 {self.failed} 条 · 未处理 {self.unprocessed} 条"
        return text + (f" · 已取消 {self.cancelled} 条" if self.cancelled else "")

    def text(self, *, cancelled=False) -> str:
        pending = "未应用" if cancelled else "待应用"
        lines = [
            self.counts_text,
            f"已应用 {self.applied} 条 · 未采纳 {self.rejected} 条 · {pending} {self.unapplied} 条",
        ]
        if self.reasons:
            lines.append("未完成原因：" + "；".join(f"{reason} {count} 条" for reason, count in self.reasons))
        return "\n".join(lines)


def summarize_entries(entries, *, source=None) -> TaskSummary:
    rows = tuple(row for row in entries.values() if source is None or row.source == source)
    succeeded = tuple(row for row in rows if row.status == "succeeded")
    failed = tuple(row for row in rows if row.status == "failed")
    reasons = Counter(failure_reason(row.reason) for row in failed)
    return TaskSummary(
        succeeded=len(succeeded),
        review=sum(row.stage == 2 for row in succeeded),
        failed=len(failed),
        unprocessed=sum(row.status == "not_started" for row in rows),
        cancelled=sum(row.status == "cancelled" for row in rows),
        applied=sum(row.applied for row in rows),
        rejected=sum(row.decision == "rejected" for row in succeeded),
        unapplied=sum(not row.applied and row.decision != "rejected" for row in succeeded),
        reasons=tuple(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
    )
