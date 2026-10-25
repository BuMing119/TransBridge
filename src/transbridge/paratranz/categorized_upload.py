"""Retained ordinary-upload sync compatibility gateway.

The GUI publishes through FilePublicationGateway. This adapter retains the
reviewed string operations used by OrdinaryUploadAdapter, plus file-list and
cancellation helpers shared with the current file publication boundary.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import logging
from pathlib import PurePosixPath
from threading import Event
from time import monotonic

from urllib3.fields import RequestField
from urllib3.filepost import encode_multipart_formdata

from transbridge.application.categorized_upload import RemoteEntry, RemoteFile
from transbridge.application.categorized_upload.diagnostics import log_event, log_failure
from transbridge.application.categorized_upload.file_creation import imported_entries, validate_creation_entries
from transbridge.application.categorized_upload.models import FileCreationRejected
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.security.diagnostic_logging import diagnostic_credentials
from transbridge.converter.translation_entry import TranslationEntry

from .api.paratranz_files_api import _make_file_field
from .categorized_response_diagnostics import error_response_summary, response_summary
from .pagination import SnapshotPagination
from .paratranz_client import ParatranzClient, RetryPolicy


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


class CategorizedUploadGateway(ParatranzClient):
    """Ordinary sync compatibility with verified writes and no blind POST retries."""

    def __init__(
        self, config, *, cancel: Event | None = None, session=None, status: Callable[[str], None] | None = None
    ) -> None:
        super().__init__(config=config, session=session, retry_policy=RetryPolicy(max_attempts=1))
        self.cancellation = UploadCancellation(cancel or Event())
        self._expected_entries: dict[int, RemoteEntry] = {}
        self._status = status or (lambda _message: None)

    def _request(self, method, endpoint, **kwargs):
        started = monotonic()
        with diagnostic_credentials(self.config.token or ""):
            try:
                result = super()._request(method, endpoint, **kwargs)
            except Exception as exc:
                log_failure(
                    "http_request",
                    exc,
                    method=method,
                    endpoint=endpoint,
                    response=error_response_summary(exc, redact=self._safe),
                )
                raise
            log_event(
                "http_response",
                level=logging.DEBUG if method.upper() == "GET" else logging.INFO,
                method=method,
                endpoint=endpoint,
                elapsed_ms=round((monotonic() - started) * 1000),
                response=response_summary(result, redact=self._safe),
            )
            return result

    def read_file_path(self, project_id: int, file_id: int) -> str | None:
        _positive(file_id, "file id")
        self._status(f"核验文件路径（文件 ID {file_id}）")
        try:
            data = self._read(f"/projects/{project_id}/files/{file_id}")
        except ExternalServiceError as exc:
            if exc.category is ExternalServiceCategory.NOT_FOUND and exc.status == 404:
                return None
            raise
        if not isinstance(data, Mapping) or _positive(data.get("id"), "file id") != file_id:
            raise ValueError("云端文件身份与请求不一致，未执行写入")
        return _file_path(data)

    def find_files(self, project_id: int, path: str) -> tuple[RemoteFile, ...]:
        path = _safe_path(path)
        self._status("写入前核验分类路径")
        files = _complete_files(self._read(f"/projects/{project_id}/files"))
        ids = [_positive(file.get("id"), "file id") for file in files]
        if len(ids) != len(set(ids)):
            raise ValueError("云端文件列表包含重复 ID，未执行写入")
        return tuple(
            RemoteFile(file_id, path, self._file_entries(project_id, file_id, phase="核验同路径文件"))
            for file, file_id in zip(files, ids, strict=True)
            if _file_path(file) == path
        )

    def read_entry(self, project_id: int, remote_id: int) -> RemoteEntry | None:
        try:
            return self._get_entry(project_id, remote_id, phase="写入前核验词条")
        except ExternalServiceError as exc:
            if exc.category is ExternalServiceCategory.NOT_FOUND and exc.status == 404:
                return None
            raise

    def create_file(self, project_id: int, path: str, entries: tuple[TranslationEntry, ...]) -> RemoteFile:
        normalized = _safe_path(path)
        validate_creation_entries(entries)
        self._status(f"上传分类文件（{len(entries)} 条词条）")
        log_event("create_file_request", project_id=project_id, path=normalized, submitted_entries=len(entries))
        target = PurePosixPath(normalized)
        payload = json.dumps([_wire_entry(entry) for entry in entries], ensure_ascii=False).encode("utf-8")
        fields = [_make_file_field("file", target.name, payload)]
        folder = "" if str(target.parent) == "." else str(target.parent) + "/"
        field = RequestField(name="path", data=folder)
        field.make_multipart()
        fields.append(field)
        body, content_type = encode_multipart_formdata(fields)
        result = self._request_multipart(
            "POST", f"/projects/{project_id}/files", body, content_type, cancellation=self.cancellation
        )
        if isinstance(result, Mapping) and result.get("status") == "empty":
            raise FileCreationRejected("ParaTranz 返回 status=empty，未接受分类文件内容；已停止后续上传，请检查格式。")
        if not isinstance(result, Mapping):
            raise ValueError("云端可能已创建文件，但未返回文件身份；本次不会重复创建，再次上传会先自动核验")
        data = result.get("file", result)
        if not isinstance(data, Mapping) or "id" not in data:
            raise ValueError("云端可能已创建文件，但未返回文件身份；本次不会重复创建，再次上传会先自动核验")
        file_id = _positive(data.get("id"), "new file id")
        if _file_path(data) != normalized:
            raise ValueError("云端已返回文件，但路径与计划不一致；再次上传会先自动核验")
        created = RemoteFile(file_id, normalized, self._file_entries(project_id, file_id, phase="回读核实新建分类文件"))
        imported_entries(normalized, entries, created)
        self._expected_entries.update((entry.remote_id, entry) for entry in created.entries)
        return created

    def create_entry(self, project_id: int, file_id: int, entry: TranslationEntry) -> RemoteEntry:
        _positive(file_id, "target file id")
        context = entry.context or ""
        payload = {"fileId": file_id, **_wire_entry(entry)}
        self._status(f"上传新增词条（文件 ID {file_id}）")
        result = self._request("POST", f"/projects/{project_id}/strings", json=payload, cancellation=self.cancellation)
        if not isinstance(result, Mapping):
            raise ValueError("云端可能已创建词条，但未返回身份；再次上传会先自动核验")
        created = self._get_entry(project_id, _positive(result.get("id"), "new entry id"), phase="回读核实新增词条")
        if (created.file_id, created.key, created.original, created.translation, created.context, created.stage) != (
            file_id,
            entry.key,
            entry.original,
            entry.translation or "",
            context,
            entry.stage,
        ):
            raise ValueError("云端新建词条与提交内容不一致；再次上传会先自动核验")
        self._expected_entries[created.remote_id] = created
        return created

    def update_translation(self, project_id: int, remote_id: int, translation: str, stage: int) -> RemoteEntry:
        before = self._get_entry(project_id, remote_id, phase="写入前核查词条")
        self._check_reviewed_entry(before)
        self._status(f"上传词条更新（词条 ID {remote_id}）")
        self._request(
            "PUT",
            f"/projects/{project_id}/strings/{remote_id}",
            json={"translation": translation, "stage": stage},
            cancellation=self.cancellation,
        )
        after = self._get_entry(project_id, remote_id, phase="回读核实词条更新")
        if (after.file_id, after.key, after.original, after.context) != (
            before.file_id,
            before.key,
            before.original,
            before.context,
        ) or (after.translation, after.stage) != (translation, stage):
            raise ValueError("云端更新结果与提交内容不一致；再次上传会先自动核验")
        self._expected_entries[after.remote_id] = after
        return after

    def _check_reviewed_entry(self, current: RemoteEntry) -> None:
        expected = self._expected_entries.get(current.remote_id)
        if expected is not None and current != expected:
            raise ExternalServiceError(ExternalServiceCategory.CONFLICT, "云端词条在执行期间已变化，已保留当前内容")

    def _read(self, endpoint: str, **kwargs):
        return self._request("GET", endpoint, cancellation=self.cancellation, **kwargs)

    def _get_entry(self, project_id: int, remote_id: int, *, phase: str) -> RemoteEntry:
        _positive(remote_id, "entry id")
        self._status(f"{phase}（词条 ID {remote_id}）")
        result = _entry(self._read(f"/projects/{project_id}/strings/{remote_id}"))
        if result.remote_id != remote_id:
            raise ValueError("云端返回了不同的词条 ID")
        return result

    def _file_entries(self, project_id: int, file_id: int, *, phase: str) -> tuple[RemoteEntry, ...]:
        result = []
        seen: set[int] = set()
        pagination = SnapshotPagination()
        page_size = 800
        for page in range(1, 1001):
            self._status(f"{phase}，文件 ID {file_id}：第 {page} 页，已读取 {len(result)} 条")
            payload = self._read(
                f"/projects/{project_id}/strings", params={"file": file_id, "page": page, "pageSize": page_size}
            )
            rows = _items(payload, "results", "strings")
            for row in rows:
                entry = _entry(row, file_id=file_id)
                if entry.file_id != file_id or entry.remote_id in seen:
                    raise ValueError("云端分页内容重复或跨文件变化，本次未使用不完整数据")
                seen.add(entry.remote_id)
                result.append(entry)
            if _complete_page(
                pagination, payload, page=page, received=len(rows), accumulated=len(result), requested_size=page_size
            ):
                return tuple(result)
        raise ValueError("分类文件超过完整分页读取上限，未创建上传计划")


def _wire_entry(entry: TranslationEntry) -> dict:
    return {
        "key": entry.key,
        "original": entry.original,
        "translation": entry.translation or "",
        "context": entry.context or "",
        "stage": entry.stage,
    }


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


def _positive(value, label) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"云端 {label} 必须是正整数")
    return value


def _entry(value, *, file_id=None) -> RemoteEntry:
    if not isinstance(value, Mapping):
        raise ValueError("云端词条无效")
    raw_file = value.get("fileId", value.get("file", file_id))
    if isinstance(raw_file, Mapping):
        raw_file = raw_file.get("id")
    target_file = _positive(raw_file, "entry file id")
    fields = []
    for key in ("key", "original", "translation", "context"):
        field = value.get(key, "")
        if not isinstance(field, str) or (key == "key" and not field):
            raise ValueError(f"云端词条 {key} 无效")
        fields.append(field)
    stage = value.get("stage", 0)
    if isinstance(stage, bool) or not isinstance(stage, int):
        raise ValueError("云端词条状态无效")
    return RemoteEntry(_positive(value.get("id"), "entry id"), target_file, *fields, stage)


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
