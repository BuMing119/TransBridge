# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Compose ordinary/category file uploads from authoritative project sources."""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
from threading import Event, Lock
from uuid import uuid4

from transbridge.application.categorized_upload import UploadSource
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
from transbridge.application.upload_policy import POLICY_FILENAME
from transbridge.paratranz.file_transport import UploadCancellation
from transbridge.paratranz.project_policy import ProjectUploadGateway, publish_policy, read_access


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
    selected: bool = True


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
    unchanged_count: int = 0

    @property
    def updated_count(self) -> int:
        return self.success_count - self.unchanged_count


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
        select_sources=False,
        gateway_factory=None,
        check_allowed=None,
    ) -> None:
        self.context = context
        self.check_allowed = check_allowed
        self.source_namespace = source_namespace
        self.select_sources = select_sources
        slots = () if select_sources else tuple(getattr(context, "slots", {}).values())
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
        self.source_ids = tuple(sorted(ids)) if not select_sources and hasattr(context, "slots") else None
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
        self.gateway_factory = gateway_factory or ProjectUploadGateway
        self.stamp = upload_target_identity(context, self.target)
        self._upload_lock = Lock()
        self.snapshot = None
        self._retry_selection = None
        self._retry_files = None
        self._retry_categorized = categorized
        self.session_id = uuid4().hex

    def policy_access(self, cancel, *, previous=None, policy=None):
        self._check_target()
        gateway = self.gateway_factory(self.config, cancel=cancel)
        try:
            pid = self.target.project_id
            if policy is None:
                access = read_access(gateway, pid, self.target.account_user_id, gateway.inventory(pid), for_window=True)
            else:
                access = publish_policy(gateway, pid, previous, policy, self._check_target)
            self._check_target()
            return access
        finally:
            gateway.close()

    def check_policy_target(self):
        self._check_target()

    def load_source_catalog(self, progress, cancel: Event):
        self._check_target()
        if cancel.is_set():
            return ()
        progress(0, 0, "正在读取当前版本的插件目录…")
        catalog = _value(self.commands.file_sources_catalog(self.request_context))
        slot = getattr(self.context, "active_slot", None)
        active_id = getattr(slot, "source_id", None)
        namespace = getattr(slot, "source_namespace", None)
        if not active_id and not namespace:
            namespaces = {entry.identity.namespace.value for entry in getattr(self.context, "collection", None) or ()}
            namespace = next(iter(namespaces)) if len(namespaces) == 1 else None
        selected = next((item.source_id for item in catalog if item.source_id == active_id), None)
        if selected is None and namespace:
            selected = next(
                (item.source_id for item in catalog if getattr(item.namespace, "value", None) == namespace), None
            )
        return tuple(
            SourceOption(item.source_id, item.label, (), len(catalog) == 1 or item.source_id == selected)
            for item in catalog
        )

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

    def load_preview(self, progress, cancel: Event, *, source_ids=None, categorized=None):
        categorized = self.categorized if categorized is None else categorized
        with self._diagnostic_scope("preview"):
            sources = self._load_preview(progress, cancel, source_ids, categorized)
            log_event(
                "preview_summary",
                sources=len(sources),
                files=sum(len(source.files) for source in sources),
                entries=sum(file.count for source in sources for file in source.files),
                cancelled=cancel.is_set(),
            )
            return sources

    def _load_preview(self, progress, cancel: Event, source_ids, categorized):
        self._check_target()
        if cancel.is_set():
            return ()
        progress(0, 0, "正在读取当前版本的工程来源…")
        snapshot = self._capture(cancel, source_ids=source_ids, categorized=categorized)
        sources = self._sources_in_scope(snapshot)
        files = self._files_for_snapshot(snapshot, categorized)
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

    def upload(self, selection, progress, cancel: Event, *, categorized=None):
        categorized = self.categorized if categorized is None else categorized
        return self._run_upload(selection, progress, cancel, retry=False, categorized=categorized)

    def retry_upload(self, progress, cancel: Event):
        return self._run_upload(None, progress, cancel, retry=True, categorized=None)

    def _run_upload(self, selection, progress, cancel, *, retry, categorized):
        if not self._upload_lock.acquire(blocking=False):
            raise RuntimeError("已有分类上传正在执行，请等待完成或取消。")
        try:
            if retry:
                selection, categorized = self._retry_selection, self._retry_categorized
            if selection is None or retry and self._retry_files == ():
                raise ValueError("没有可补传的未完成文件。")
            if not retry:
                self._retry_selection = selection
                self._retry_files = None
                self._retry_categorized = categorized
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
                return self._upload(selection, progress, cancel, categorized)
        finally:
            self._upload_lock.release()

    def _upload(self, selection, progress, cancel: Event, categorized):
        self._check_target()
        if cancel.is_set():
            return UploadReceipt(
                0, 0, details=("已取消，尚未读取云端或上传内容。",), retry_count=len(selection.file_keys)
            )
        progress(0, 0, "正在读取本次上传的工程来源和文件…")
        self._retry_files = self._selected_files(selection, cancel, categorized=categorized)
        return self._execute_upload(self._retry_files, selection.translation_mode, progress, cancel)

    def _selected_files(self, selection, cancel, *, categorized=None):
        categorized = self.categorized if categorized is None else categorized
        source_ids = set(selection.source_ids)
        if not source_ids or not selection.file_keys:
            raise ValueError("请至少选择一个插件及一个文件。")
        snapshot = self._capture(cancel, source_ids=selection.source_ids, categorized=categorized)
        if not source_ids <= {source.source_id for source in self._sources_in_scope(snapshot)}:
            raise ValueError("所选插件不属于本次上传范围，请重新选择。")
        available = {file_key(file): file for file in self._files_for_snapshot(snapshot, categorized)}
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

            def check_policy(paths):
                access = read_access(gateway, self.target.project_id, self.target.account_user_id, paths)
                if not access.allows_layout(self._retry_categorized) or not access.allows(mode):
                    raise PermissionError("项目上传政策未允许当前账号使用所选布局或操作。")

            receipt = execute_file_upload(
                self.target.project_id,
                files,
                gateway,
                mode=mode,
                cancelled=cancel.is_set,
                progress=progress,
                check_current=self._check_current,
                check_inventory=check_policy,
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
            unchanged_count=sum(item.status == "unchanged" for item in receipt.files),
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

    def _files_for_snapshot(self, snapshot, categorized):
        sources = snapshot.sources
        if self.select_sources:
            captured = {source.source_id for source in sources}
            # Include unselected names when resolving path collisions without loading their contents.
            sources = (
                *sources,
                *(
                    UploadSource(source_id, label, ())
                    for source_id, label in snapshot.source_names
                    if source_id not in captured
                ),
            )
        return tuple(
            file for file in upload_files(sources, categorized=categorized) if file.path.casefold() != POLICY_FILENAME
        )

    def _capture(self, cancel=None, *, source_ids=None, categorized=None):
        return _value(
            self.commands.categorized_upload_snapshot(
                self.request_context,
                endpoint=self.target.endpoint,
                remote_project_id=self.target.project_id,
                source_ids=source_ids if self.select_sources else self.source_ids,
                include_dialogue_order=self.categorized if categorized is None else categorized,
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
