"""Translate synchronization receipts into item-based completion feedback."""

from dataclasses import dataclass

from transbridge.application.contracts import OperationResult
from transbridge.application.tasks import JobState


@dataclass(frozen=True, slots=True)
class SyncReceipt:
    status: str
    text: str
    details: str


def sync_receipt(activity, result: OperationResult | None, *, action: str) -> SyncReceipt:
    status = {JobState.FAILED: "failed", JobState.CANCELLED: "cancelled"}.get(
        activity.state, result.outcome.value if result is not None else "unknown"
    )
    headings = {
        "completed": f"{action}完成。",
        "partial": "同步部分完成，仍有条目需要处理。",
        "cancelled": "同步已取消。",
        "failed": "同步失败。",
        "unknown": "同步结果待核验。",
    }
    lines = [headings[status]]
    details = [f"任务 ID：{activity.run_id}"]
    if result is None:
        lines.append("未取得执行结果，请在任务中心查看详情。")
        details.extend(item.code for item in activity.diagnostic_refs)
    else:
        counts = result.counts
        lines.append(
            f"成功 {counts.succeeded:,} · 失败 {counts.failed:,} · 跳过 {counts.skipped:,} · 取消 {counts.cancelled:,}"
        )
        if status != "completed" and counts.succeeded:
            lines.append("已成功提交的结果予以保留。")
        actionable = [item for item in result.diagnostics if item.code != "SYNC_RETRY_AVAILABLE"]
        if actionable:
            lines.append(actionable[0].message)
        details.extend(f"{item.code}：{item.message}" for item in actionable)
    if status != "completed":
        lines.append("再次执行前请重新检查；更多详情见任务中心。")
    return SyncReceipt(status, "\n".join(lines), "\n".join(details))
