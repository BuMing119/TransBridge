"""Compose ordinary/category file uploads from authoritative project sources."""

from __future__ import annotations

from copy import copy
from dataclasses import replace
from threading import Event, Lock
from uuid import uuid4

from PyQt6.QtWidgets import QMessageBox

from transbridge.application.categorized_upload.diagnostics import diagnostic_scope, log_event
from transbridge.application.file_upload import execute_file_upload, file_key, upload_files
from transbridge.application.projects.remote_binding import normalize_paratranz_endpoint
from transbridge.paratranz.file_publication import FilePublicationGateway

from .categorized_upload_dialog import (
    CategorizedUploadDialog,
    FileOption,
    SourceOption,
    UploadReceipt,
)
from .categorized_upload_feedback import UploadProgress


class CategorizedUploadSession:
    """Freeze the upload target; capture current content for each direct upload."""

    def __init__(
        self, context, *, source_namespace=None, categorized=True, gateway_factory=FilePublicationGateway
    ) -> None:
        self.context = context
        self.source_namespace = source_namespace
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
        self.gateway_factory = gateway_factory
        self.stamp = self._target_stamp()
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
        snapshot = self._capture()
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
        snapshot = self._capture()
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
        self._retry_files = tuple(available[key] for key in selection.file_keys)
        return self._execute_upload(self._retry_files, selection.translation_mode, progress, cancel)

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

    def _capture(self):
        return _value(
            self.commands.categorized_upload_snapshot(
                self.request_context,
                endpoint=self.target.endpoint,
                remote_project_id=self.target.project_id,
            )
        )

    def _target_stamp(self):
        target = self.context.resolve_paratranz_target()
        return (
            normalize_paratranz_endpoint(target.endpoint),
            target.project_id,
            target.account_user_id,
            self.context.active_project_id,
            self.context.active_variant_id,
        )

    def _check_target(self):
        if self._target_stamp() != self.stamp:
            raise ValueError("工程、翻译版本、云端项目或账号已变化，请重新打开上传。")

    def _check_current(self):
        self._check_target()
        _value(self.commands.categorized_upload_current(self.snapshot, self.request_context))


def _value(result):
    if not result.is_success or result.value is None:
        messages = "；".join(item.message for item in result.diagnostics)
        raise RuntimeError(messages or "无法读取文件上传的工程状态。")
    return result.value


def open_categorized_upload(context, parent=None, *, batch: bool = False):
    return open_file_upload(context, parent, batch=batch, categorized=True)


def open_file_upload(context, parent=None, *, batch=False, categorized=False, **values):
    try:
        if values:
            from .download_context import DownloadContext

            context = DownloadContext(context, values)
        namespace = None
        if not batch:
            namespaces = {entry.identity.namespace for entry in context.collection or ()}
            if len(namespaces) != 1:
                raise ValueError("单独分类上传需要当前插件的唯一来源，请先选择一个插件。")
            namespace = namespaces.pop()
        session = CategorizedUploadSession(context, source_namespace=namespace, categorized=categorized)
    except (ValueError, RuntimeError, AttributeError) as exc:
        QMessageBox.warning(parent, "无法上传", str(exc))
        return None
    dialog = CategorizedUploadDialog(
        context.project_name or "当前工程",
        str(context.active_variant or "当前版本"),
        session.target.project_name or str(session.target.project_id),
        load_preview=session.load_preview,
        upload=session.upload,
        retry_upload=session.retry_upload,
        batch=batch,
        categorized=categorized,
        parent=parent,
    )
    dialog.show()
    return dialog
