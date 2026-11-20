# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Coalesce files whose only outstanding write is an in-place string rename."""

from dataclasses import replace
from threading import Lock

from transbridge.application.categorized_upload.diagnostics import log_failure, log_timing
from transbridge.application.categorized_upload.models import CategorizedFile
from transbridge.application.file_upload import FileReceipt, FileUploadReceipt
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError


class PartialRenameError(ExternalServiceError):
    """Some requested string IDs were confirmed before the batch stopped."""

    def __init__(self, confirmed_ids, cause: ExternalServiceError):
        super().__init__(
            cause.category, f"已有 {len(confirmed_ids)} 项改号确认，剩余项须重新比较。{cause}", status=cause.status
        )
        self.confirmed_ids = frozenset(confirmed_ids)


class PendingFileRenames:
    def __init__(self, mode: str):
        self._mode = mode
        self._lock = Lock()
        self._files: dict[str, tuple[CategorizedFile, tuple[dict, ...]]] = {}

    def add(self, file, items) -> FileReceipt:
        with self._lock:
            self._files[file.path] = (file, tuple(items))
        return FileReceipt(file.path, "pending_rename", "已完成比较，等待批量更新排序码。")

    def finish(self, receipt, project_id, port, cancelled, check_current) -> FileUploadReceipt:
        pending = [row for row in receipt.files if row.status == "pending_rename"]
        if not pending:
            return receipt
        items = tuple(item for row in pending for item in self._files[row.path][1])
        updates = {}
        attempted = False
        try:
            if cancelled() or any(row.http_status in {401, 403, 429} for row in receipt.files):
                raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "本轮已停止，待处理改号未发送。")
            check_current()
            if len({item["id"] for item in items}) != len(items):
                raise ValueError("多个文件申领同一云端词条 ID，无法批量更新排序码。")
            attempted = True
            port.rename_keys(project_id, items)
        except Exception as exc:
            log_failure("file_upload_batch_rename", exc, project_id=project_id)
            confirmed = getattr(exc, "confirmed_ids", frozenset())
            http_status = getattr(exc, "status", None)
            if getattr(exc, "category", None) is ExternalServiceCategory.CANCELLED:
                state = "cancelled"
            elif attempted and (http_status is None or http_status >= 500):
                state = "uncertain"
            else:
                state = "failed"
            for row in pending:
                ids = {item["id"] for item in self._files[row.path][1]}
                if ids <= confirmed:
                    updates[row.path] = FileReceipt(row.path, "succeeded", "已更新排序码。", True)
                else:
                    updates[row.path] = FileReceipt(
                        row.path, state, f"排序码更新未完成：{exc}", bool(ids & confirmed), http_status=http_status
                    )
            if http_status == 404 and not confirmed:
                return self._relocate(receipt, pending, project_id, port, cancelled, check_current)
        else:
            updates = {row.path: FileReceipt(row.path, "succeeded", "已更新排序码。", True) for row in pending}
        retries = list(receipt.retry_files)
        for path, row in updates.items():
            if row.status != "succeeded":
                retries.append(self._files[path][0])
            log_timing("file_upload_result", path=path, status=row.status, source_applied=row.source_applied)
        return replace(
            receipt, files=tuple(updates.get(row.path, row) for row in receipt.files), retry_files=tuple(retries)
        )

    def _relocate(self, receipt, pending, project_id, port, cancelled, check_current):
        # The batch 404 does not identify the replaced file. Refresh the shared
        # listing once, then resolve each affected path with a fresh comparison.
        from .file_upload import UploadInventory, _publish_parts, wire_records

        results = {}
        retries = list(receipt.retry_files)
        try:
            check_current()
            if cancelled():
                raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "已取消重新定位。")
            inventory = UploadInventory(port.inventory(project_id), allow_refresh=False)
        except Exception as exc:
            log_failure("file_rename_relocate", exc, project_id=project_id)
            state = "cancelled" if getattr(exc, "category", None) is ExternalServiceCategory.CANCELLED else "failed"
            for row in pending:
                results[row.path] = (
                    FileReceipt(row.path, state, f"重新定位失败：{exc}", http_status=getattr(exc, "status", None)),
                )
                retries.append(self._files[row.path][0])
        else:
            stopped = False
            for row in pending:
                file = self._files[row.path][0]
                if stopped or cancelled():
                    results[row.path] = (FileReceipt(row.path, "skipped", "本轮已停止。"),)
                    retries.append(file)
                    continue
                parts = _publish_parts(
                    project_id, file, wire_records(file), inventory, port, self._mode, cancelled, check_current, retries
                )
                results[row.path] = parts
                stopped = any(item.http_status in {401, 403, 429} for item in parts)
        return FileUploadReceipt(
            tuple(item for row in receipt.files for item in results.get(row.path, (row,))), tuple(retries)
        )
