# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""File-scoped ParaTranz publication and force-import comparison."""

from collections import Counter
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from threading import Lock
from typing import Protocol

from transbridge.application.categorized_upload.classification import categorize_sources, source_directory_names
from transbridge.application.categorized_upload.diagnostics import log_event, log_failure
from transbridge.application.categorized_upload.models import CategorizedFile, UploadSource
from transbridge.application.io.paratranz_context_order import context_orders, format_ordered_context
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry

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

    def translate(
        self, project_id: int, file_id: int, path: str, records: tuple[dict, ...], *, force: bool
    ) -> FileWriteResult: ...


class UploadInventory:
    """Resolve full paths and serialize 404 refreshes across file pipelines."""

    def __init__(self, inventory: dict[str, int]):
        self._paths = self._index(inventory)
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
            tuple(
                replace(entry, context=format_ordered_context(entry.context, order))
                for entry, order in zip(source.entries, context_orders(source.entries), strict=True)
            ),
        )
        for source in sources
        if source.entries
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
    records = tuple(paratranz_record_from_entry(replace(entry, external_refs=())) for entry in file.entries)
    counts = Counter(record["key"] for record in records)
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
    """One inventory, at most two writes per file before splitting; no local commits.

    Only an explicit 404 permits one path refresh/replan. Timeouts are uncertain,
    never replayed. Force imports compare against the current target file first.
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
        prepare = getattr(port, "force_comparison", None) if mode == "trans_force" else None
        if prepare is not None:
            selected_ids = _comparison_file_ids(files, inventory)
            status = (lambda message: progress(0, len(files), message)) if progress else None
            try:
                archive_reader = resources.enter_context(prepare(project_id, paths, selected_ids, progress=status))
            except Exception as exc:
                log_failure("force_upload_archive_prepare", exc, project_id=project_id)
                was_cancelled = cancelled() or (
                    isinstance(exc, ExternalServiceError) and exc.category is ExternalServiceCategory.CANCELLED
                )
                state = "cancelled" if was_cancelled else "failed"
                message = "已取消强制导入。" if was_cancelled else f"无法准备云端压缩包，本次未强制导入：{exc}"
                return FileUploadReceipt(tuple(FileReceipt(file.path, state, message) for file in files), files)
        return _publish_files(
            project_id, files, port, inventory, mode, cancelled, check_current, progress, archive_reader
        )


def _comparison_file_ids(files, inventory):
    selected_ids = set()
    for file in files:
        if not any(entry.translation.strip() for entry in file.entries):
            continue
        try:
            _path, file_id = inventory.resolve(file.path)
        except ValueError:
            # The ordinary publish path reports this file's ambiguity.
            continue
        if file_id is not None:
            selected_ids.add(file_id)
    return selected_ids


def _publish_files(project_id, files, port, inventory, mode, cancelled, check_current, progress, archive_reader):
    if getattr(port, "max_workers", 1) > 1 and len(files) > 1:
        from .file_upload_parallel import publish_files_parallel

        return publish_files_parallel(
            project_id, files, port, inventory, mode, cancelled, check_current, progress, archive_reader=archive_reader
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
            progress(index, len(files), f"正在上传文件：{file.path}")
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
    project_id, file, records, inventory, port, mode, cancelled, check_current, retry_files, *, archive_reader=None
):
    """Retain deterministic 413 splitting without remapping keys or source order."""
    target = PurePosixPath(file.path)
    counter = 0

    def publish(rows, entries, split=False):
        nonlocal counter
        part_path = str(target.with_name(f"{target.stem}_{counter + 1}{target.suffix}")) if split else file.path
        result = _upload_one(
            project_id, part_path, rows, inventory, port, mode, cancelled, check_current, archive_reader=archive_reader
        )
        if result.status == "too_large" and len(rows) > 1:
            middle = len(rows) // 2
            return (*publish(rows[:middle], entries[:middle], True), *publish(rows[middle:], entries[middle:], True))
        counter += 1
        if result.status == "too_large":
            result = replace(result, status="failed", message="单条词条仍超过服务器文件大小限制。")
        if result.status not in {"succeeded", "unchanged"}:
            retry_files.append(replace(file, path=part_path, entries=entries))
        return (result,)

    return publish(records, file.entries)


def _upload_one(project_id, path, records, inventory, port, mode, cancelled, check_current, *, archive_reader=None):
    source_applied = translation_applied = False
    attempted = False
    phase = "source"
    for attempt in range(2):
        try:
            remote_path, file_id = inventory.resolve(path)
        except ValueError as exc:
            log_failure("file_upload_resolve", exc, path=path, project_id=project_id)
            return FileReceipt(path, "failed", str(exc), source_applied, translation_applied)
        try:
            if mode in {"trans_safe", "trans_force"} and file_id is None:
                return FileReceipt(path, "skipped", "仅导入译文：云端没有此文件，已跳过。")
            results = []
            if mode in {"orig_only", "both"}:
                check_current()
                if cancelled():
                    return FileReceipt(path, "cancelled", "已取消。", source_applied, translation_applied)
                attempted = True
                if file_id is None:
                    result = port.create(project_id, path, records)
                    if result.file_id is None:
                        raise ValueError("创建响应未返回文件 ID，结果待核实。")
                    file_id = result.file_id
                    inventory.record_created(path, file_id)
                else:
                    result = port.update(project_id, file_id, path, records)
                results.append(result.status)
                source_applied = result.status == "applied"
            if mode != "orig_only":
                phase = "translation"
                check_current()
                if cancelled():
                    return FileReceipt(path, "cancelled", "已取消后续译文导入。", source_applied, translation_applied)
                force = mode == "trans_force"
                translations = records
                if force:
                    phase = "translation_check"
                    # A post-time 404 can invalidate the archive's file identity.
                    read = archive_reader if attempt == 0 and archive_reader is not None else port.get_file_translation
                    translations = _force_translation_changes(project_id, file_id, records, read)
                    check_current()
                    if cancelled():
                        return FileReceipt(path, "cancelled", "已取消强制导入译文。")
                    if not translations:
                        return FileReceipt(path, "unchanged", "没有可强制导入的非空差异译文。")
                    phase = "translation"
                attempted = True
                result = _translate_parts(
                    project_id, file_id, path, translations, port, force, cancelled, check_current
                )
                results.append(result.status)
                translation_applied = result.status == "applied"
            unchanged = all(status in {"hashMatched", "unchanged"} for status in results)
            message = "服务端确认无需更新。" if unchanged else "文件操作完成。"
            if mode in {"both", "trans_safe"} and not unchanged:
                message += "译文使用安全导入，人工译文等由服务端保留。"
            return FileReceipt(
                path, "unchanged" if unchanged else "succeeded", message, source_applied, translation_applied
            )
        except ExternalServiceError as exc:
            log_failure("file_upload_request", exc, path=path, project_id=project_id)
            if exc.status == 404 and attempt == 0:
                # Deleted/recreated files are ordinary path resolution, not an ID conflict.
                try:
                    inventory.relocate(port, project_id, remote_path)
                except Exception as refresh_error:
                    log_failure("file_upload_relocate", refresh_error, path=path)
                    return FileReceipt(
                        path, "failed", f"重新定位文件失败：{refresh_error}", source_applied, translation_applied
                    )
                continue
            if exc.status == 413 and phase != "translation_check":
                if phase == "translation":
                    return FileReceipt(path, "failed", "译文单项超过服务器大小限制，未创建其他文件。", source_applied)
                return FileReceipt(path, "too_large", "文件过大。", source_applied, translation_applied)
            if exc.category is ExternalServiceCategory.CANCELLED:
                status, message = "cancelled", "请求已取消；已提交的文件操作可能已生效。"
            elif phase == "translation_check":
                status, message = "failed", f"无法读取云端译文进行比较，本次未强制导入：{exc}"
            elif exc.status == 403:
                status, message = "failed", "权限不足：文件译文导入需要管理员以上权限，未回退到词条接口。"
            elif exc.status == 423:
                status, message = "failed", "文件正在被其他用户更新或回滚，请稍后重试。"
            else:
                uncertain = exc.status is None or exc.status >= 500
                status = "uncertain" if uncertain else "failed"
                message = f"文件操作{'结果待核实，不自动重发' if uncertain else '失败'}：{exc}"
            if source_applied:
                message = "原文已提交；" + message
            return FileReceipt(path, status, message, source_applied, translation_applied, exc.status)
        except Exception as exc:
            log_failure("file_upload_request", exc, path=path, project_id=project_id)
            if phase == "translation_check":
                return FileReceipt(path, "failed", f"无法比较云端译文，本次未强制导入：{exc}")
            return FileReceipt(
                path,
                "uncertain" if attempted else "failed",
                f"文件操作未完整完成：{exc}",
                source_applied,
                translation_applied,
            )
    raise RuntimeError("unreachable file upload state")


def _force_translation_changes(project_id, file_id, records, read):
    candidates = {row["key"]: row for row in records if row["translation"].strip()}
    if not candidates:
        return ()
    rows = read(project_id, file_id)
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("云端文件词条数据无效。")

    cloud = {}
    for row in rows:
        entry = ParaTranzEntry.from_mapping(row)
        if entry.file_id is not None and entry.file_id != file_id:
            raise ValueError("云端返回了目标文件以外的词条。")
        if entry.key in candidates:
            if entry.key in cloud or "translation" not in row:
                raise ValueError("云端词条 key 重复或缺少译文，无法安全比较。")
            cloud[entry.key] = entry.translation
    return tuple(row for key, row in candidates.items() if key in cloud and row["translation"] != cloud[key])


def _translate_parts(project_id, file_id, path, records, port, force, cancelled, check_current):
    """An oversized translation import is chunked inside the SAME remote file."""
    check_current()
    if cancelled():
        raise ExternalServiceError(ExternalServiceCategory.CANCELLED, "译文导入已取消。")
    try:
        return port.translate(project_id, file_id, path, records, force=force)
    except ExternalServiceError as exc:
        if exc.status != 413 or len(records) <= 1:
            raise
    middle = len(records) // 2
    first = _translate_parts(project_id, file_id, path, records[:middle], port, force, cancelled, check_current)
    try:
        second = _translate_parts(project_id, file_id, path, records[middle:], port, force, cancelled, check_current)
    except Exception as exc:
        # Do not replay completed chunks, or split into unrelated remote files.
        raise RuntimeError("部分译文已处理，后续导入未完成；请查看文件结果。") from exc
    applied = first.status == "applied" or second.status == "applied"
    return FileWriteResult("applied" if applied else "unchanged", file_id)
