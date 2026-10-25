"""Resolve current plugin files by path, independently of persisted upload IDs."""

from collections.abc import Mapping
from pathlib import PurePosixPath

from transbridge.application.io.paratranz_classification import classify_entries
from transbridge.application.ports.paratranz import ParaTranzEntry

from .download_paths import (
    legacy_plugin_paths,
    matches_current_path as matches_current_path,
    select_download_files,
    validate_download_path as _validate_path,
)
from .file_publication import FilePublicationGateway
from .sync_snapshot import _snapshot


class CurrentPathSnapshotAdapter:
    def __init__(
        self,
        config,
        root_path,
        *,
        category_paths=(),
        gateway_factory=None,
        legacy_paths=None,
        blocked_legacy_paths=(),
    ):
        self.config = config
        self.root_path = root_path
        self.paths = frozenset((root_path, *category_paths))
        self.legacy_paths = legacy_plugin_paths(self.paths) if legacy_paths is None else tuple(legacy_paths)
        self.blocked_legacy_paths = tuple(blocked_legacy_paths)
        for path in (*self.paths, *self.legacy_paths, *self.blocked_legacy_paths):
            _validate_path(path)
        self.gateway_factory = gateway_factory or FilePublicationGateway

    def fetch(self, project_id, namespace, *, limit, cancellation=None):
        gateway = self.gateway_factory(self.config)
        try:
            if cancellation is not None:
                gateway.cancellation = cancellation
            inventory = gateway.inventory(project_id)
            snapshots = []
            seen_ids = set()
            files = select_download_files(
                inventory, self.paths, self.legacy_paths, blocked_legacy_paths=self.blocked_legacy_paths
            )
            for path, file_id in files:
                # Official file data endpoint is complete and unpaginated.
                rows = gateway.get_file_translation(project_id, file_id)
                if not isinstance(rows, list) or not all(isinstance(row, Mapping) for row in rows):
                    raise ValueError("云端文件词条数据无效，未使用不完整内容。")
                for row in rows:
                    entry = ParaTranzEntry.from_mapping(row)
                    if entry.file_id is not None and entry.file_id != file_id:
                        raise ValueError("云端返回了目标文件以外的词条。")
                    if entry.remote_id is not None:
                        if entry.remote_id in seen_ids:
                            raise ValueError("云端文件返回重复词条 ID。")
                        seen_ids.add(entry.remote_id)
                    snapshots.append(_snapshot(entry, namespace, f"project:{project_id}", decode_keys=True))
                if len(snapshots) > limit:
                    raise ValueError("remote snapshot exceeds the configured complete-plan limit")
            return tuple(snapshots)
        finally:
            gateway.close()


def current_category_paths(root_path, entries):
    """Use the upload classifier against the complete source, before selection."""
    _validate_path(root_path)
    directory = str(PurePosixPath(root_path).with_suffix(""))
    return tuple(f"{directory}/{group.path}" for group in classify_entries(tuple(entries)))
