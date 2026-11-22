# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compose ordinary/category file uploads from authoritative project sources."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from threading import Event, Lock
from uuid import uuid4

from transbridge.application.categorized_upload.diagnostics import diagnostic_scope, log_event
from transbridge.application.file_upload import (
    UploadInventory,
    execute_file_upload,
    file_key,
    upload_files,
    wire_records,
)
from transbridge.application.file_upload_execution import comparison_plan
from transbridge.application.projects.remote_binding import normalize_paratranz_endpoint
from transbridge.application.projects.source_content import source_content_identity
from transbridge.paratranz.file_publication import FilePublicationGateway
from transbridge.paratranz.file_transport import UploadCancellation


@dataclass(frozen=True, slots=True)
class FileOption:
    file_key: str
    path: str
    count: int


@dataclass(frozen=True, slots=True)
class SourceOption:
    source_id: str
    label: str
    files: tuple[FileOption, ...]


@dataclass(frozen=True, slots=True)
class Selection:
    source_ids: tuple[str, ...]
    file_keys: tuple[str, ...]
    translation_mode: str = "orig_only"


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    success_count: int
    failed_count: int
    cancelled_count: int = 0
    details: tuple[str, ...] = ()
    pending_count: int = 0
    retry_count: int = 0


class UploadProgress:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.current = self.total = 0

    def __call__(self, current: int, total: int, message: str) -> None:
        self.current, self.total = current, total
        self.callback(current, total, message)

    def status(self, message: str) -> None:
        self.callback(self.current, self.total, message)


def upload_target_identity(context, target):
    return (
        normalize_paratranz_endpoint(target.endpoint),
        target.project_id,
        target.account_user_id,
        context.active_project_id,
        context.active_variant_id,
    )


class CategorizedUploadSession:
    """Freeze the upload target; capture current content for each direct upload."""

    def __init__(
        self,
        context,
        *,
        source_namespace=None,
        source_ids=None,
        categorized=True,
        gateway_factory=None,
        check_allowed=None,
    ) -> None:
        self.context = context
        self.check_allowed = check_allowed
        self.source_namespace = source_namespace
        slots = tuple(getattr(context, "slots", {}).values())
        if source_namespace is not None:
            slots = tuple(
                slot for slot in slots if any(entry.identity.namespace == source_namespace for entry in slot.collection)
            )
        ids = {slot.source_id for slot in slots if getattr(slot, "source_id", None)}
        namespaces = {entry.identity.namespace.value for slot in slots for entry in slot.collection}
        ids.update(
            source["source_id"]
            for source in getattr(context, "project_sources", ())
            if source_content_identity(source) in namespaces
        )
        registered_namespaces = {source_content_identity(source) for source in getattr(context, "project_sources", ())}
        ids.update(f"namespace:{namespace}" for namespace in namespaces - registered_namespaces)
        self.source_ids = tuple(sorted(ids)) if hasattr(context, "slots") else None
        if source_ids is not None:
            if self.source_ids is not None and not set(source_ids) <= ids:
                raise ValueError("所选来源不在当前上传范围内。")
            self.source_ids = tuple(source_ids)
        self.categorized = categorized
        self.commands = context.project_commands
        if self.commands is None or not context.uses_authoritative_projection:
            raise ValueError("文件上传需要先打开一个本地工程。")
        self.request_context = replace(
            context.runtime_context, project_id=context.active_project_id, variant_id=context.active_variant_id
        )
        self.target = context.resolve_paratranz_target()
        if self.target.project_id is None or self.target.status.value not in {"available", "unverified"}:
            raise ValueError(self.target.reason or "请先为当前工程绑定 ParaTranz 项目。")
        if self.target.account_user_id is None:
            raise ValueError("请先登录并验证 ParaTranz 账号。")
        self.config = copy(context.config)
        self.gateway_factory = gateway_factory or FilePublicationGateway
        self.stamp = upload_target_identity(context, self.target)
        self._upload_lock = Lock()
        self.snapshot = None
        self._retry_selection = None
        self._retry_files = None
        self.session_id = uuid4().hex

    def _diagnostic_scope(self, phase, **fields):
        return diagnostic_scope(
            phase,
            credential=getattr(self.config, "token", "") or "",
            session_id=self.session_id,
            project_id=self.target.project_id,
            local_project_id=self.request_context.project_id,
            variant_id=self.request_context.variant_id,
            upload_scope="batch" if self.source_namespace is None else "single",
            **fields,
        )

    def load_preview(self, progress, cancel: Event):
        with self._diagnostic_scope("preview"):
            sources = self._load_preview(progress, cancel)
            log_event(
                "preview_summary",
                sources=len(sources),
                files=sum(len(source.files) for source in sources),
                entries=sum(file.count for source in sources for file in source.files),
                cancelled=cancel.is_set(),
            )
            return sources

    def _load_preview(self, progress, cancel: Event):
        self._check_target()
        if cancel.is_set():
            return ()
        progress(0, 0, "正在读取当前版本的工程来源…")
        snapshot = self._capture(cancel)
        sources = self._sources_in_scope(snapshot)
        files = upload_files(snapshot.sources, categorized=self.categorized)
        return tuple(
            SourceOption(
                source.source_id,
                source.label,
                tuple(
                    FileOption(file_key(file), file.path, len(file.entries))
                    for file in files
                    if file.source_id == source.source_id
                ),
            )
            for source in sources
        )

    def upload(self, selection, progress, cancel: Event):
        return self._run_upload(selection, progress, cancel, retry=False)

    def retry_upload(self, progress, cancel: Event):
        return self._run_upload(self._retry_selection, progress, cancel, retry=True)

    def _run_upload(self, selection, progress, cancel, *, retry):
        if not self._upload_lock.acquire(blocking=False):
            raise RuntimeError("已有分类上传正在执行，请等待完成或取消。")
        try:
            if selection is None or retry and self._retry_files == ():
                raise ValueError("没有可补传的未完成文件。")
            if not retry:
                self._retry_selection = selection
                self._retry_files = None
            file_count = len(self._retry_files) if retry and self._retry_files is not None else len(selection.file_keys)
            source_ids, file_keys = selection.source_ids, selection.file_keys
            if retry and self._retry_files is not None:
                source_ids = tuple(dict.fromkeys(file.source_id for file in self._retry_files))
                file_keys = tuple(dict.fromkeys(file_key(file) for file in self._retry_files))
            with self._diagnostic_scope(
                "upload",
                selected_source_ids=source_ids,
                selected_file_count=file_count,
                migrate=False,
                retry=retry,
            ):
                log_event("selection", selected_file_keys=file_keys)
                if retry and self._retry_files is not None:
                    self._check_current()
                    return self._execute_upload(self._retry_files, selection.translation_mode, progress, cancel)
                return self._upload(selection, progress, cancel)
        finally:
            self._upload_lock.release()

    def _upload(self, selection, progress, cancel: Event):
        self._check_target()
        if cancel.is_set():
            return UploadReceipt(
                0, 0, details=("已取消，尚未读取云端或上传内容。",), retry_count=len(selection.file_keys)
            )
        progress(0, 0, "正在读取本次上传的工程来源和分类…")
        self._retry_files = self._selected_files(selection, cancel)
        return self._execute_upload(self._retry_files, selection.translation_mode, progress, cancel)

    def _selected_files(self, selection, cancel):
        snapshot = self._capture(cancel)
        files = upload_files(snapshot.sources, categorized=self.categorized)
        available = {file_key(file): file for file in files}
        source_ids = set(selection.source_ids)
        if not source_ids or not selection.file_keys:
            raise ValueError("请至少选择一个插件及一个分类文件。")
        if not source_ids <= {source.source_id for source in self._sources_in_scope(snapshot)}:
            raise ValueError("所选插件不属于本次上传范围，请重新选择。")
        if any(key not in available or available[key].source_id not in source_ids for key in selection.file_keys):
            raise ValueError("工程来源或分类已变化，请重新打开分类上传。")
        self.snapshot = snapshot
        self._check_current()
        return tuple(available[key] for key in selection.file_keys)

    def compare(self, selection, cancel):
        """Read the same file identities and differences, without starting a remote export or publishing."""
        self._check_target()
        files = self._selected_files(selection, cancel)
        gateway = self.gateway_factory(self.config, cancel=cancel)
        try:
            inventory = UploadInventory(gateway.inventory(self.target.project_id))
            differences = []
            for file in files:
                self._check_current()
                UploadCancellation(cancel).raise_if_cancelled()
                path, file_id = inventory.resolve(file.path)
                records = wire_records(file)
                changes = comparison_plan(self.target.project_id, file_id, records, gateway, selection.translation_mode)
                differences.append({
                    "source_id": file.source_id,
                    "file_key": file_key(file),
                    "path": path,
                    "exists": file_id is not None,
                    "source_changed": changes.source_changed,
                    "renames": len(changes.renames),
                    "translations": len(changes.translations),
                })
            return differences
        finally:
            gateway.close()

    def _execute_upload(self, files, mode, progress, cancel):
        progress = UploadProgress(progress)
        gateway = self.gateway_factory(self.config, cancel=cancel, status=progress.status)
        try:
            receipt = execute_file_upload(
                self.target.project_id,
                files,
                gateway,
                mode=mode,
                cancelled=cancel.is_set,
                progress=progress,
                check_current=self._check_current,
            )
        finally:
            gateway.close()
        self.last_receipt = receipt
        self._retry_files = receipt.retry_files
        return UploadReceipt(
            receipt.succeeded,
            receipt.failed,
            sum(item.status == "cancelled" for item in receipt.files),
            tuple(f"{item.path}：{item.message}" for item in receipt.files),
            sum(item.status == "skipped" for item in receipt.files),
            retry_count=len(receipt.retry_files),
        )

    def _sources_in_scope(self, snapshot):
        if self.source_namespace is None:
            return snapshot.sources
        sources = tuple(
            source
            for source in snapshot.sources
            if any(entry.identity.namespace == self.source_namespace for entry in source.entries)
        )
        if len(sources) != 1:
            raise ValueError("无法唯一定位当前插件的工程来源，请重新打开分类上传。")
        return sources

    def _capture(self, cancel=None):
        return _value(
            self.commands.categorized_upload_snapshot(
                self.request_context,
                endpoint=self.target.endpoint,
                remote_project_id=self.target.project_id,
                source_ids=self.source_ids,
                include_dialogue_order=self.categorized,
                cancellation=UploadCancellation(cancel) if cancel is not None else None,
            )
        )

    def _check_target(self):
        if self.check_allowed is not None:
            self.check_allowed()
        if upload_target_identity(self.context, self.context.resolve_paratranz_target()) != self.stamp:
            raise ValueError("工程、翻译版本、云端项目或账号已变化，请重新打开上传。")

    def _check_current(self):
        self._check_target()
        _value(self.commands.categorized_upload_current(self.snapshot, self.request_context))


def _value(result):
    if not result.is_success or result.value is None:
        messages = "；".join(item.message for item in result.diagnostics)
        raise RuntimeError(messages or "无法读取文件上传的工程状态。")
    return result.value
