# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Prepare ParaTranz upload files and coordinate comparison, publication and retries."""

from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
import re
from threading import Lock
from typing import Protocol

from transbridge.application.categorized_upload.classification import categorize_sources, source_directory_names
from transbridge.application.categorized_upload.diagnostics import log_event, log_failure
from transbridge.application.categorized_upload.models import CategorizedFile, UploadSource
from transbridge.application.io.paratranz_context_order import PARATRANZ_CONTEXT_ORDER_METADATA, context_orders
from transbridge.application.io.paratranz_keys import split_ordered_key
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError

UPLOAD_MODES = ("orig_only", "trans_safe", "trans_force", "both")


@dataclass(frozen=True, slots=True)
class FileWriteResult:
    status: str = "applied"
    file_id: int | None = None


@dataclass(frozen=True, slots=True)
class FileReceipt:
    path: str
    status: str
    message: str
    source_applied: bool = False
    translation_applied: bool = False
    http_status: int | None = None


@dataclass(frozen=True, slots=True)
class FileUploadReceipt:
    files: tuple[FileReceipt, ...]
    retry_files: tuple[CategorizedFile, ...] = ()

    @property
    def succeeded(self):
        return sum(item.status in {"succeeded", "unchanged"} for item in self.files)

    @property
    def failed(self):
        return sum(item.status in {"failed", "uncertain"} for item in self.files)


class FileUploadPort(Protocol):
    def inventory(self, project_id: int) -> dict[str, int]: ...

    def create(self, project_id: int, path: str, records: tuple[dict, ...]) -> FileWriteResult: ...

    def update(self, project_id: int, file_id: int, path: str, records: tuple[dict, ...]) -> FileWriteResult: ...

    def get_file_translation(self, project_id: int, file_id: int) -> list[Mapping]: ...

    def rename_keys(self, project_id: int, items: tuple[dict, ...]) -> None: ...

    def translate(
        self, project_id: int, file_id: int, path: str, records: tuple[dict, ...], *, force: bool
    ) -> FileWriteResult: ...


class UploadInventory:
    """Resolve full paths and serialize 404 refreshes across file pipelines."""

    def __init__(self, inventory: dict[str, int], *, allow_refresh=True):
        self._paths = self._index(inventory)
        self._allow_refresh = allow_refresh
        self._created: dict[str, int] = {}
        self._lock, self._refresh = Lock(), Lock()

    @staticmethod
    def _index(inventory: dict[str, int]) -> dict[str, dict[str, int]]:
        paths: dict[str, dict[str, int]] = {}
        for path, file_id in inventory.items():
            paths.setdefault(path.casefold(), {})[path] = file_id
        return paths

    def resolve(self, path: str) -> tuple[str, int | None]:
        with self._lock:
            matches = self._paths.get(path.casefold())
            if not matches:
                return path, None
            if path in matches:
                return path, matches[path]
            if len(matches) == 1:
                return next(iter(matches.items()))
            raise ValueError(f"云端存在仅大小写不同的多个路径，无法唯一定位：{'、'.join(sorted(matches))}。")

    def record_created(self, path: str, file_id: int) -> None:
        with self._lock:
            self._paths.setdefault(path.casefold(), {})[path] = file_id
            self._created[path] = file_id

    def relocate(self, port: FileUploadPort, project_id: int, remote_path: str) -> None:
        if not self._allow_refresh:
            raise ValueError("本轮已经刷新过云端文件列表。")
        with self._refresh:
            with self._lock:
                self._created.pop(remote_path, None)
            fresh = port.inventory(project_id)
            with self._lock:
                self._paths = self._index({**fresh, **self._created})


def upload_files(sources: tuple[UploadSource, ...], *, categorized: bool) -> tuple[CategorizedFile, ...]:
    """Build paths against ALL sources so single/batch naming remains identical."""
    if categorized:
        return categorize_sources(sources)
    names = source_directory_names(sources)
    return tuple(
        CategorizedFile(
            source.source_id,
            "plain",
            names[source.source_id] + ".json",
            _plain_entries(source),
        )
        for source in sources
        if source.entries
    )


def _plain_entries(source):
    complete = source.entries if source.classification_entries is None else source.classification_entries
    orders = {entry.identity: order for entry, order in zip(complete, context_orders(complete), strict=True)}
    if len(orders) != len(complete):
        raise ValueError("完整来源包含重复词条身份。")
    if not {entry.identity for entry in source.entries} <= orders.keys():
        raise ValueError("完整来源未覆盖所选词条。")
    return tuple(
        replace(
            entry,
            metadata=tuple({**dict(entry.metadata), PARATRANZ_CONTEXT_ORDER_METADATA: orders[entry.identity]}.items()),
        )
        for entry in source.entries
    )


def file_key(file: CategorizedFile) -> str:
    return f"{file.source_id}:{file.category_id}"


def wire_records(file: CategorizedFile) -> tuple[dict, ...]:
    """Never send persisted remote IDs or change local source identity."""
    path = PurePosixPath(file.path)
    if path.is_absolute() or "\\" in file.path or any(part in {"", ".", ".."} for part in file.path.split("/")):
        raise ValueError("上传文件路径无效。")
    if not file.entries:
        raise ValueError("分类为空，不上传空文件。")
    fallback = context_orders(file.entries)
    records = tuple(
        paratranz_record_from_entry(
            replace(entry, external_refs=()),
            key_order=dict(entry.metadata).get(PARATRANZ_CONTEXT_ORDER_METADATA, order),
        )
        for entry, order in zip(file.entries, fallback, strict=True)
    )
    counts = Counter(split_ordered_key(record["key"])[0] for record in records)
    if any(count > 1 for count in counts.values()):
        raise ValueError("同一文件存在无法区分的重复词条，请检查本地 key 和原文身份。")
    return records


def execute_file_upload(
    project_id: int,
    files: tuple[CategorizedFile, ...],
    port: FileUploadPort,
    *,
    mode: str = "orig_only",
    cancelled: Callable[[], bool] = lambda: False,
    check_current: Callable[[], None] = lambda: None,
    progress: Callable[[int, int, str], None] | None = None,
) -> FileUploadReceipt:
    """Compare existing files, migrate keys, then apply independent write phases.

    Only an explicit 404 permits one path refresh/replan. Timeouts are uncertain,
    never replayed. Every comparison remains scoped to the current target file.
    """
    if mode not in UPLOAD_MODES:
        raise ValueError("未知文件上传模式。")
    if type(project_id) is not int or project_id < 1:
        raise ValueError("project_id must be a positive integer")
    if len({file.path for file in files}) != len(files):
        raise ValueError("多个本地分类指向相同上传路径。")
    if cancelled():
        return FileUploadReceipt(
            tuple(FileReceipt(file.path, "cancelled", "已取消，尚未上传。") for file in files), files
        )
    check_current()
    if progress:
        progress(0, len(files), "正在获取云端文件列表…")
    paths = port.inventory(project_id)
    inventory = UploadInventory(paths)
    with ExitStack() as resources:
        archive_reader = None
        prepare = getattr(port, "force_comparison", None)
        if prepare is not None:
            selected_ids = _comparison_file_ids(files, inventory, mode)
            status = (lambda message: progress(0, len(files), message)) if progress else None
            try:
                archive_reader = resources.enter_context(prepare(project_id, paths, selected_ids, progress=status))
            except Exception as exc:
                log_failure("file_upload_archive_prepare", exc, project_id=project_id)
                was_cancelled = cancelled() or (
                    isinstance(exc, ExternalServiceError) and exc.category is ExternalServiceCategory.CANCELLED
                )
                state = "cancelled" if was_cancelled else "failed"
                message = "已取消文件上传。" if was_cancelled else f"无法准备云端压缩包，本次未上传：{exc}"
                return FileUploadReceipt(tuple(FileReceipt(file.path, state, message) for file in files), files)
        from .file_upload_renames import PendingFileRenames

        pending = PendingFileRenames(mode)
        groups = Counter(_split_group(file.path) for file in files)

        def defer(file, items):
            if groups[_split_group(file.path)] == 1:
                return pending.add(file, items)
            return None

        receipt = _publish_files(
            project_id,
            files,
            port,
            inventory,
            mode,
            cancelled,
            check_current,
            progress,
            archive_reader,
            defer if len(files) > 1 and mode in {"orig_only", "both"} else None,
        )
        return pending.finish(receipt, project_id, port, cancelled, check_current)


def _split_group(path):
    return re.sub(r"(?:_\d+)+$", "", str(PurePosixPath(path).with_suffix("")).casefold())


def _comparison_file_ids(files, inventory, mode="trans_force"):
    selected_ids = set()
    for file in files:
        if mode == "trans_force" and not any(entry.translation.strip() for entry in file.entries):
            continue
        try:
            _path, file_id = inventory.resolve(file.path)
        except ValueError:
            # The ordinary publish path reports this file's ambiguity.
            continue
        if file_id is not None:
            selected_ids.add(file_id)
    return selected_ids


def _publish_files(
    project_id, files, port, inventory, mode, cancelled, check_current, progress, archive_reader, defer_renames=None
):
    if getattr(port, "max_workers", 1) > 1 and len(files) > 1:
        from .file_upload_parallel import publish_files_parallel

        return publish_files_parallel(
            project_id,
            files,
            port,
            inventory,
            mode,
            cancelled,
            check_current,
            progress,
            archive_reader=archive_reader,
            defer_renames=defer_renames,
        )
    receipts = []
    retry_files = []
    stopped = ""
    for index, file in enumerate(files):
        if cancelled():
            receipts.append(FileReceipt(file.path, "cancelled", "已取消后续文件。"))
            retry_files.append(file)
            continue
        if stopped:
            receipts.append(FileReceipt(file.path, "skipped", stopped))
            retry_files.append(file)
            continue
        if progress:
            progress(index, len(files), f"正在核对文件：{file.path}")
        try:
            check_current()
        except Exception as exc:
            log_failure("file_upload_target", exc, path=file.path)
            stopped = f"本地工程或目标已变化，后续文件未上传：{exc}"
            receipts.append(FileReceipt(file.path, "skipped", stopped))
            retry_files.append(file)
            continue
        try:
            records = wire_records(file)
        except (TypeError, ValueError) as exc:
            log_failure("file_upload_prepare", exc, path=file.path)
            receipts.append(FileReceipt(file.path, "failed", str(exc)))
            retry_files.append(file)
            continue
        parts = _publish_parts(
            project_id,
            file,
            records,
            inventory,
            port,
            mode,
            cancelled,
            check_current,
            retry_files,
            archive_reader=archive_reader,
            defer_renames=defer_renames,
        )
        receipts.extend(parts)
        for item in parts:
            log_event(
                "file_upload_result",
                path=item.path,
                status=item.status,
                source_applied=item.source_applied,
                translation_applied=item.translation_applied,
            )
        receipt = parts[-1]
        if any(item.http_status in {401, 403, 429} for item in parts):
            stopped = "账号权限或请求限流未恢复，后续文件未上传。"
        if progress:
            progress(index + 1, len(files), f"{file.path}：{receipt.message}")
    return FileUploadReceipt(tuple(receipts), tuple(retry_files))


def _publish_parts(
    project_id,
    file,
    records,
    inventory,
    port,
    mode,
    cancelled,
    check_current,
    retry_files,
    *,
    archive_reader=None,
    defer_renames=None,
):
    """Retain deterministic 413 splitting without remapping keys or source order."""
    target = PurePosixPath(file.path)
    counter = 0

    def publish(rows, entries, split=False):
        nonlocal counter
        if split:
            entries = tuple(
                replace(
                    entry,
                    metadata=tuple(
                        {
                            **dict(entry.metadata),
                            PARATRANZ_CONTEXT_ORDER_METADATA: split_ordered_key(row["key"])[1],
                        }.items()
                    ),
                )
                for entry, row in zip(entries, rows, strict=True)
            )
        part_path = str(target.with_name(f"{target.stem}_{counter + 1}{target.suffix}")) if split else file.path
        result = _upload_one(
            project_id,
            part_path,
            rows,
            inventory,
            port,
            mode,
            cancelled,
            check_current,
            archive_reader=archive_reader if not split else None,
            defer_renames=(
                (lambda items: defer_renames(file, items)) if not split and defer_renames is not None else None
            ),
        )
        if result.status == "too_large" and len(rows) > 1:
            middle = len(rows) // 2
            return (*publish(rows[:middle], entries[:middle], True), *publish(rows[middle:], entries[middle:], True))
        counter += 1
        if result.status == "too_large":
            result = replace(result, status="failed", message="单条词条仍超过服务器文件大小限制。")
        if result.status not in {"succeeded", "unchanged", "pending_rename"}:
            retry_files.append(replace(file, path=part_path, entries=entries))
        return (result,)

    return publish(records, file.entries)


def _upload_one(*args, **kwargs):
    from .file_upload_execution import upload_one

    return upload_one(*args, **kwargs)
