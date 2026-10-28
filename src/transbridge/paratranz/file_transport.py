"""Cancellation and complete file inventory validation for file transfers."""

from collections.abc import Mapping
from threading import Event

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError

from .pagination import SnapshotPagination


class UploadCancellation:
    def __init__(self, event: Event) -> None:
        self.event = event

    @property
    def is_cancelled(self) -> bool:
        return self.event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.event.wait(timeout)

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "分类上传已取消")


def _items(payload, *keys) -> list[Mapping]:
    if isinstance(payload, Mapping):
        payload = next((payload[key] for key in keys if key in payload), None)
    if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
        raise ValueError("云端返回的分类文件或词条列表无效")
    return payload


def _complete_page(pagination, payload, *, page, received, accumulated, requested_size) -> bool:
    """Keep every declared total authoritative before trusting missing identities."""
    metadata = payload if isinstance(payload, Mapping) else {}
    for field in ("page", "pageSize", "pageCount", "rowCount"):
        if field in metadata and metadata[field] is None:
            raise ValueError(f"云端分页的 {field} 声明无效")
    complete = pagination.complete(
        payload, page=page, received=received, accumulated=accumulated, requested_size=requested_size
    )
    total, pages = pagination.total, pagination.pages
    if "pageSize" in metadata and received > metadata["pageSize"]:
        raise ValueError("云端分页返回条数超过声明的 pageSize")
    if total is not None:
        if accumulated > total or (not received and accumulated < total):
            raise ValueError("云端分页返回条数与声明总数不一致")
        complete = accumulated == total
    elif pages is not None:
        complete = page >= pages
    if pages is not None:
        if (
            (pages == 0 and (accumulated or page != 1))
            or (pages > 0 and page > pages)
            or (complete and page < pages)
            or (not complete and page >= pages)
            or (page > 1 and not received)
        ):
            raise ValueError("云端分页当前页、总页数与返回条数矛盾")
        if total is not None and "pageSize" in metadata:
            expected_pages = (total + metadata["pageSize"] - 1) // metadata["pageSize"]
            if pages not in ({0, 1} if total == 0 else {expected_pages}):
                raise ValueError("云端分页总页数、总条数与 pageSize 矛盾")
    return complete


def _complete_files(payload) -> list[Mapping]:
    """Only a complete project file listing can prove stored identities absent."""
    files = _items(payload, "files", "results")
    if not isinstance(payload, Mapping):
        return files
    if not _complete_page(
        SnapshotPagination(),
        payload,
        page=1,
        received=len(files),
        accumulated=len(files),
        requested_size=len(files) + 1,
    ):
        raise ValueError("云端文件列表未完整返回或分页声明矛盾，无法确认文件是否缺失；本次未执行写入。")
    return files


def _safe_path(value) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("分类文件路径无效")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("分类文件路径包含无效片段")
    return value


def _file_path(value: Mapping) -> str:
    name = value.get("name")
    folder = value.get("folder", "")
    if not isinstance(name, str) or not isinstance(folder, str):
        raise ValueError("云端文件路径无效")
    return _safe_path(name if "/" in name or not folder else f"{folder.rstrip('/')}/{name}")
