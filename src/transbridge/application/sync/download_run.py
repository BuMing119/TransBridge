"""Run the user's download request through comparison and durable publication."""

from collections.abc import Callable
from dataclasses import replace

from transbridge.application.tasks import TaskCancelled

from .download_view import DownloadPreview, DownloadReceipt


def run_download(
    source_ids: tuple[str, ...],
    policy: str,
    decisions: tuple[tuple[str, str], ...],
    compare: Callable,
    apply: Callable,
    progress: Callable,
    cancel,
) -> DownloadPreview | DownloadReceipt:
    """Continue safe work automatically; preserve unresolved rows for review.

    The caller owns one worker and cancellation event for the entire run.
    Application callbacks retain this round's input, local merge and commit guards.
    At most two refreshed plans are automatically attempted after the first.
    """
    preview = compare(source_ids, policy, decisions, progress, cancel)
    for attempt in range(3):
        if cancel.is_set():
            raise TaskCancelled("下载已取消，本地译文未修改。")
        if not preview.can_save and (not preview.can_apply or preview.updates <= 0):
            return preview
        receipt = apply(progress, cancel)
        receipt = replace(receipt, retryable=receipt.retryable or preview.retryable)
        if not receipt.requires_review:
            if not preview.deferred:
                return receipt
            pending = replace(
                preview,
                updates=0,
                unchanged=preview.unchanged + receipt.updated,
                can_apply=False,
                can_save=False,
                status="当前本地版本已保存，待确认项保留本地内容。",
            )
            return replace(receipt, requires_review=True, preview=pending)
        if receipt.updated or receipt.preview is None:
            return receipt
        preview = receipt.preview
        if attempt == 2 and (preview.can_save or preview.can_apply and preview.updates > 0):
            return replace(
                receipt,
                retryable=True,
                preview=replace(preview, status="本地内容持续变化，本次尚未保存；选择已保留，可原地重试。"),
            )
    return receipt
