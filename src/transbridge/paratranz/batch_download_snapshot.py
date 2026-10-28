"""Read complete current files once and isolate failures by registered source."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import logging

from transbridge.application.io.identity import SourceNamespace
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.application.sync.models import RemoteEntrySnapshot

from .download_paths import (
    legacy_plugin_paths,
    matches_current_path,
    preferred_download_paths,
    select_download_files,
    unambiguous_legacy_paths,
    validate_download_path,
)
from .file_publication import FilePublicationGateway
from .sync_snapshot import _snapshot

_logger = logging.getLogger(__name__)
_GLOBAL_CATEGORIES = {
    ExternalServiceCategory.AUTHENTICATION,
    ExternalServiceCategory.AUTHORIZATION,
    ExternalServiceCategory.RATE_LIMITED,
    ExternalServiceCategory.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class BatchDownloadScope:
    source_id: str
    namespace: SourceNamespace
    paths: tuple[str, ...]
    legacy_paths: tuple[str, ...] | None = None
    blocked_legacy_paths: tuple[str, ...] = ()

    def __post_init__(self):
        if not self.source_id or not isinstance(self.namespace, SourceNamespace):
            raise ValueError("下载来源缺少稳定身份。")
        if not self.paths:
            raise ValueError("下载来源缺少当前文件路径。")
        aliases = legacy_plugin_paths(self.paths) if self.legacy_paths is None else tuple(self.legacy_paths)
        for path in (*self.paths, *aliases, *self.blocked_legacy_paths):
            validate_download_path(path)
        object.__setattr__(self, "paths", tuple(dict.fromkeys(self.paths)))
        object.__setattr__(self, "legacy_paths", tuple(dict.fromkeys(aliases)))
        object.__setattr__(self, "blocked_legacy_paths", tuple(dict.fromkeys(self.blocked_legacy_paths)))


@dataclass(frozen=True, slots=True)
class BatchDownloadRead:
    source_id: str
    entries: tuple[RemoteEntrySnapshot, ...] = ()
    paths: tuple[str, ...] = ()
    error: str = ""


class BatchDownloadSnapshotReader:
    def __init__(self, config, *, gateway_factory=None):
        self.config = config
        self.gateway_factory = gateway_factory or FilePublicationGateway

    def fetch(self, project_id, scopes, *, cancellation=None, progress=None, limit=100_000, on_source=None):
        scopes = tuple(scopes)
        if type(project_id) is not int or project_id < 1 or type(limit) is not int or limit < 1:
            raise ValueError("下载项目或完整快照上限无效。")
        if len({scope.source_id for scope in scopes}) != len(scopes):
            raise ValueError("批量下载包含重复来源。")
        if len({scope.namespace for scope in scopes}) != len(scopes):
            raise ValueError("批量下载来源身份不唯一。")
        allowed = unambiguous_legacy_paths({scope.source_id: scope.paths for scope in scopes})
        scopes = tuple(
            replace(
                scope,
                legacy_paths=tuple(path for path in scope.legacy_paths if path in allowed[scope.source_id]),
                blocked_legacy_paths=tuple(
                    dict.fromkeys((
                        *scope.blocked_legacy_paths,
                        *(path for path in scope.legacy_paths if path not in allowed[scope.source_id]),
                    ))
                ),
            )
            for scope in scopes
        )
        _check_cancelled(cancellation)
        if not scopes:
            return ()
        gateway = self.gateway_factory(self.config)
        try:
            if cancellation is not None:
                gateway.cancellation = cancellation
            inventory = _inventory(gateway, project_id)
            results = []
            owners = {}
            collided = set()
            consumed = 0
            for index, scope in enumerate(scopes):
                _check_cancelled(cancellation)
                if progress:
                    progress(index, len(scopes), f"正在读取 {scope.paths[0]} 等当前文件…")
                try:
                    read, remote_ids, inventory = _read_source(
                        gateway, project_id, scope, scopes, inventory, cancellation, limit - consumed
                    )
                except Exception as exc:
                    if isinstance(exc, _PartialReadFailure):
                        if on_source is not None:
                            on_source(scope, exc.read)
                        exc = exc.cause
                    _check_cancelled(cancellation)
                    if _global_failure(exc):
                        raise exc
                    _logger.warning("Batch download source failed: source_id=%s", scope.source_id, exc_info=True)
                    results.append(BatchDownloadRead(scope.source_id, error=str(exc)))
                    continue
                for remote_id in remote_ids:
                    previous = owners.setdefault(remote_id, scope.source_id)
                    if previous != scope.source_id:
                        collided.update((previous, scope.source_id))
                consumed += len(read.entries)
                results.append(read)
                if on_source is not None:
                    on_source(scope, read)
            _check_cancelled(cancellation)
            if progress:
                progress(len(scopes), len(scopes), "当前文件读取完成。")
            completed = tuple(
                replace(item, entries=(), error="云端不同插件返回了重复词条 ID，本次未采用这些插件的内容。")
                if item.source_id in collided
                else item
                for item in results
            )
            if on_source is not None:
                for scope, read in zip(scopes, completed, strict=True):
                    if read.source_id in collided:
                        on_source(scope, read)
            return completed
        finally:
            gateway.close()


def _read_source(gateway, project_id, scope, scopes, inventory, cancellation, remaining):
    """Keep failed-source file results as evidence, never as a complete snapshot."""
    selected_paths = preferred_download_paths(inventory, scope.paths, scope.legacy_paths)
    if selected_paths:
        # A read-time 404 may refresh IDs/partitions, never switch generations.
        scope = replace(scope, paths=selected_paths, legacy_paths=())
    cache = {}
    try:
        for attempt in range(2):
            try:
                return _read_source_files(
                    gateway, project_id, scope, scopes, inventory, cancellation, remaining, cache, attempt == 0
                )
            except _SourceInventoryRefreshed:
                # Rebuild current partitions; unchanged file IDs reuse parsed results.
                continue
        raise RuntimeError("云端文件持续变化，本次未采用该插件的部分内容。")
    except Exception as exc:
        if cache:
            read = BatchDownloadRead(
                scope.source_id,
                tuple(entry for entries in cache.values() for entry in entries),
                tuple(dict.fromkeys(path for path, _file_id in cache)),
                f"来源未完整读取，仅保留已下载文件：{exc}",
            )
            raise _PartialReadFailure(read, exc) from exc
        raise


class _PartialReadFailure(RuntimeError):
    def __init__(self, read, cause):
        super().__init__(str(cause))
        self.read, self.cause = read, cause


class _SourceInventoryRefreshed(RuntimeError):
    """A bounded internal restart after an ordinary file-ID relocation."""


def _read_source_files(gateway, project_id, scope, scopes, inventory, cancellation, remaining, cache, relocate):
    files = _source_files(scope, scopes, inventory)
    snapshots, remote_ids, paths = [], set(), []
    for path, file_id in files:
        _check_cancelled(cancellation)
        try:
            key = (path, file_id)
            if key not in cache:
                rows = _read_file(gateway, project_id, path, file_id, inventory, scope, scopes, relocate)
                cache[key] = _file_entries(rows, file_id, scope.namespace, project_id)
            entries = cache[key]
            for entry in entries:
                if entry.external_ref is not None:
                    remote_id = entry.external_ref.opaque_id
                    if remote_id in remote_ids:
                        raise ValueError("云端文件返回重复词条 ID。")
                    remote_ids.add(remote_id)
            snapshots.extend(entries)
            if len(snapshots) > remaining:
                raise ValueError("本轮完整下载快照超过配置的上限，未采用该插件的部分内容。")
        except _SourceInventoryRefreshed:
            raise
        except Exception as exc:
            _check_cancelled(cancellation)
            if _global_failure(exc):
                raise
            raise ValueError(f"{path}：{exc}") from exc
        paths.append(path)
    return BatchDownloadRead(scope.source_id, tuple(snapshots), tuple(paths)), remote_ids, inventory


def _read_file(gateway, project_id, path, file_id, inventory, scope, scopes, relocate):
    try:
        return gateway.get_file_translation(project_id, file_id)
    except ExternalServiceError as exc:
        if not relocate or exc.status != 404 and exc.category is not ExternalServiceCategory.NOT_FOUND:
            raise
        fresh = _inventory(gateway, project_id)
        inventory.clear()
        inventory.update(fresh)
        # Re-resolve by the exact path; an unrelated file never replaces it.
        if not _source_files(scope, scopes, inventory):
            raise ValueError("云端文件刚刚被移除，本次未采用该插件的部分内容。") from exc
        raise _SourceInventoryRefreshed from exc


def _source_files(scope, scopes, inventory):
    files = select_download_files(
        inventory, scope.paths, scope.legacy_paths, blocked_legacy_paths=scope.blocked_legacy_paths
    )
    others = tuple(
        (
            frozenset(candidate.casefold() for candidate in other.paths),
            frozenset(candidate.casefold() for candidate in other.legacy_paths),
        )
        for other in scopes
        if other.source_id != scope.source_id
    )
    for path, _file_id in files:
        if any(
            matches_current_path(path.casefold(), current)
            or matches_current_path(path.casefold(), legacy)
            and not any(matches_current_path(candidate.casefold(), current) for candidate in inventory)
            for current, legacy in others
        ):
            raise ValueError(f"{path}：当前文件同时对应多个插件，本次未采用这些插件的内容。")
    return files


def _inventory(gateway, project_id):
    inventory = gateway.inventory(project_id)
    if not isinstance(inventory, Mapping):
        raise ValueError("云端文件列表格式无效。")
    ids = set()
    for path, file_id in inventory.items():
        if not isinstance(path, str) or type(file_id) is not int or file_id < 1 or file_id in ids:
            raise ValueError("云端文件列表包含无效路径或重复文件 ID。")
        ids.add(file_id)
    return dict(inventory)


def _file_entries(rows, file_id, namespace, project_id):
    if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
        raise ValueError("云端文件词条数据无效，未使用不完整内容。")
    result = []
    for row in rows:
        entry = ParaTranzEntry.from_mapping(row)
        if entry.file_id is not None and entry.file_id != file_id:
            raise ValueError("云端返回了目标文件以外的词条。")
        result.append(_snapshot(entry, namespace, f"project:{project_id}", decode_keys=True))
    return tuple(result)


def _check_cancelled(cancellation):
    if cancellation is not None:
        cancellation.raise_if_cancelled()


def _global_failure(exc):
    return isinstance(exc, ExternalServiceError) and (
        exc.status in {401, 403, 429} or exc.category in _GLOBAL_CATEGORIES
    )
