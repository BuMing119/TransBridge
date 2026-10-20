"""Translate synchronization receipts into item-based completion feedback."""

from collections.abc import Mapping
from dataclasses import dataclass

from transbridge.application.contracts import OperationResult
from transbridge.application.tasks import JobState

from .plan_view import OperationKind
from .sync_review_direction import sync_review_direction


@dataclass(frozen=True, slots=True)
class SyncReceipt:
    status: str
    text: str
    details: str


def sync_receipt(
    activity, result: OperationResult | None, *, action: str, kind: OperationKind = OperationKind.DOWNLOAD
) -> SyncReceipt:
    direction = sync_review_direction(kind)
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
        value = result.value
        deferred = value.get("deferred_count", 0) if isinstance(value, Mapping) else getattr(value, "deferred_count", 0)
        if deferred:
            lines.append(
                f"已{direction.verb} {counts.succeeded:,} · 暂缓 {deferred:,}（保持{direction.retained_side}不变）"
            )
            if counts.failed or counts.cancelled:
                lines.append(f"失败 {counts.failed:,} · 取消 {counts.cancelled:,}")
            lines.append(f"可继续打开“处理待处理项”选择处理方式，再检查并{direction.verb}。")
        else:
            lines.append(
                f"成功 {counts.succeeded:,} · 失败 {counts.failed:,} · "
                f"跳过 {counts.skipped:,} · 取消 {counts.cancelled:,}"
            )
        if status != "completed" and counts.succeeded:
            lines.append("已成功提交的结果予以保留。")
        if any(item.code == "UPLOAD_ASSOCIATION_COMMIT_FAILED" for item in result.diagnostics):
            lines.append("云端更改已提交，但本地云端关联未保存；请重新检查后再继续。")
        actionable = [item for item in result.diagnostics if item.code != "SYNC_RETRY_AVAILABLE"]
        untranslated = [item for item in actionable if item.code != "UPLOAD_ASSOCIATION_COMMIT_FAILED"]
        if untranslated:
            lines.append(untranslated[0].message)
        details.extend(f"{item.code}：{item.message}" for item in actionable)
    if status != "completed":
        if kind is OperationKind.UPLOAD:
            lines.append("已提交到云端的更改不会自动回滚，请重新检查云端实际结果。")
        lines.append("再次执行前请重新检查；更多详情见任务中心。")
    return SyncReceipt(status, "\n".join(lines), "\n".join(details))
