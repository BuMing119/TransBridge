# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Own temporary export files and expose verified entry JSON to the snapshot reader."""

import json
import logging
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from transbridge.application.ports.paratranz import ParaTranzEntry

from .artifact_download import FreshArtifactDownload
from .artifact_member_index import selected_members

ARTIFACT_FILE_THRESHOLD = 50
_MAX_FILE_BYTES = 64 * 1024**2
_MAX_SELECTED_BYTES = 512 * 1024**2
_logger = logging.getLogger(__name__)


class ArtifactFileReads:
    def __init__(self, config, *, downloader_factory=FreshArtifactDownload):
        self.downloader = downloader_factory(config)
        self._temporary = None
        self._files = {}
        self._archive = None
        self._read_bytes = 0

    def prepare(self, project_id, inventory, selected_ids, *, cancellation=None, progress=None):
        self.project_id, self.cancellation = project_id, cancellation
        self._inventory = dict(inventory)
        self._temporary = TemporaryDirectory(prefix="transbridge-paratranz-download-")
        root = Path(self._temporary.name)
        target = root / "export.zip"
        self.downloader.download(project_id, target, cancellation=cancellation, progress=progress)
        self._check_cancelled()
        if progress is not None:
            progress("正在检索所选文件…")
        selected = {file_id: path for path, file_id in inventory.items() if file_id in selected_ids}
        try:
            self._archive = ZipFile(target)
            members = _members(self._archive)
            expected = selected_members(members, inventory, selected)
            minimum_sizes = {
                file_id: min(members[name].file_size for name in names) for file_id, names in expected.items()
            }
            if sum(minimum_sizes.values()) > _MAX_SELECTED_BYTES:
                raise ValueError("压缩包所选文件解压内容超过 512 MiB 上限。")
            if any(size > _MAX_FILE_BYTES for size in minimum_sizes.values()):
                raise ValueError("压缩包所选单文件超过 64 MiB 上限。")
            self._files = expected
        except BadZipFile as exc:
            raise ValueError("ParaTranz 压缩包损坏，未采用其内容。") from exc
        self._check_cancelled()
        if progress is not None:
            progress("索引完成，正在按优先目录读取所选文件和词条…")

    def get_file_translation(self, project_id, file_id):
        self._check_cancelled()
        if project_id != self.project_id or file_id not in self._files:
            raise ValueError("压缩包读取超出本轮所选项目或文件。")
        failures = []
        path = self._inventory_path(file_id)
        for name in self._files[file_id]:
            self._check_cancelled()
            try:
                payload = self._read_member(name)
            except BadZipFile as exc:
                _logger.warning("Archive candidate damaged: %s: %s", name, exc)
                failures.append((name, exc))
                continue
            try:
                # The stdlib recognizes JSON's UTF-8/16/32 encodings, including BOMs.
                rows = json.loads(payload)
                _validate_entries(rows, file_id, complete=name not in {f"raw/{path}.json", f"raw/{path}"})
            except (ValueError, UnicodeError) as exc:
                _logger.debug("Archive candidate rejected: %s: %s", name, exc)
                failures.append((name, exc))
                continue
            if failures:
                _logger.info("Archive file recovered from fallback: %s -> %s", path, name)
            return rows
        details = "；".join(f"{name}：{exc}" for name, exc in failures[:5])
        raise ValueError(f"压缩包文件不是完整词条 JSON：{path}（{details}）") from failures[0][1]

    def _read_member(self, name):
        info = self._archive.getinfo(name)
        if info.file_size > _MAX_FILE_BYTES:
            raise ValueError(f"压缩包单文件超过 64 MiB 上限：{name}")
        if self._read_bytes + info.file_size > _MAX_SELECTED_BYTES:
            raise ValueError("压缩包累计读取内容超过 512 MiB 上限。")
        payload = bytearray()
        with self._archive.open(info) as source:
            while chunk := source.read(64 * 1024):
                self._check_cancelled()
                payload.extend(chunk)
                self._read_bytes += len(chunk)
                if len(payload) > _MAX_FILE_BYTES or self._read_bytes > _MAX_SELECTED_BYTES:
                    raise ValueError(f"压缩包读取内容超过解压上限：{name}")
        return payload

    def _inventory_path(self, file_id):
        return next(path for path, value in self._inventory.items() if value == file_id)

    def _check_cancelled(self):
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()

    def close(self):
        self._files.clear()
        if self._archive is not None:
            self._archive.close()
            self._archive = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


def _validate_entries(rows, file_id, *, complete):
    required = {"key", "original", "translation"}
    if complete:
        required.add("stage")
    if not isinstance(rows, list):
        raise ValueError("需要包含原文、译文和状态的完整词条列表")
    for row in rows:
        if not isinstance(row, dict) or not required <= row.keys():
            raise ValueError("缺少完整词条字段：" + "/".join(sorted(required)))
        entry = ParaTranzEntry.from_mapping(row)
        if entry.file_id is not None and entry.file_id != file_id:
            raise ValueError("云端返回了目标文件以外的词条。")


def _members(archive):
    result = {}
    for info in archive.infolist():
        name = info.orig_filename
        path = PurePosixPath(name)
        if (
            "\\" in name
            or "\x00" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} or ":" in part for part in name.rstrip("/").split("/"))
            or stat.S_ISLNK(info.external_attr >> 16)
        ):
            raise ValueError("压缩包包含不安全的成员路径。")
        if name in result:
            raise ValueError(f"压缩包包含重复路径：{name}")
        result[name] = info
    return {name: info for name, info in result.items() if not info.is_dir()}
