# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Own temporary export files and expose selected raw JSON to the snapshot reader."""

import json
from pathlib import Path, PurePosixPath
import stat
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from .artifact_download import FreshArtifactDownload

ARTIFACT_FILE_THRESHOLD = 50
_MAX_FILE_BYTES = 64 * 1024**2
_MAX_SELECTED_BYTES = 512 * 1024**2


class ArtifactFileReads:
    def __init__(self, config, *, downloader_factory=FreshArtifactDownload):
        self.downloader = downloader_factory(config)
        self._temporary = None
        self._files = {}

    def prepare(self, project_id, inventory, selected_ids, *, cancellation=None, progress=None):
        self.project_id, self.cancellation = project_id, cancellation
        self._inventory = dict(inventory)
        self._temporary = TemporaryDirectory(prefix="transbridge-paratranz-download-")
        root = Path(self._temporary.name)
        target = root / "export.zip"
        self.downloader.download(project_id, target, cancellation=cancellation, progress=progress)
        self._check_cancelled()
        if progress is not None:
            progress("正在解压所选文件…")
        selected = {file_id: path for path, file_id in inventory.items() if file_id in selected_ids}
        try:
            with ZipFile(target) as archive:
                members = _members(archive)
                expected = _selected_members(members, inventory, selected)
                if sum(members[name].file_size for name in expected.values()) > _MAX_SELECTED_BYTES:
                    raise ValueError("压缩包所选文件解压内容超过 512 MiB 上限。")
                for file_id, name in expected.items():
                    self._check_cancelled()
                    info = members[name]
                    if info.file_size > _MAX_FILE_BYTES:
                        raise ValueError(f"压缩包单文件超过 64 MiB 上限：{selected[file_id]}")
                    # Remote paths never become filesystem destinations (including Windows ADS/device names).
                    local = root / f"file-{file_id}.json"
                    size = 0
                    with archive.open(info) as source, local.open("xb") as output:
                        while chunk := source.read(64 * 1024):
                            self._check_cancelled()
                            size += len(chunk)
                            if size > _MAX_FILE_BYTES:
                                raise ValueError(f"压缩包单文件超过解压上限：{selected[file_id]}")
                            output.write(chunk)
                    self._files[file_id] = local
        except BadZipFile as exc:
            raise ValueError("ParaTranz 压缩包损坏，未采用其内容。") from exc
        self._check_cancelled()
        if progress is not None:
            progress("解压完成，正在匹配所选文件和词条…")

    def get_file_translation(self, project_id, file_id):
        self._check_cancelled()
        if project_id != self.project_id or file_id not in self._files:
            raise ValueError("压缩包读取超出本轮所选项目或文件。")
        try:
            return json.loads(self._files[file_id].read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"压缩包文件不是有效 JSON：{self._inventory_path(file_id)}") from exc

    def _inventory_path(self, file_id):
        return next(path for path, value in self._inventory.items() if value == file_id)

    def _check_cancelled(self):
        if self.cancellation is not None:
            self.cancellation.raise_if_cancelled()

    def close(self):
        self._files.clear()
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None


def _selected_members(members, inventory, selected):
    """Prefer ParaTranz's appended suffix; accept unchanged JSON names only when unambiguous."""
    result = {}
    for file_id, path in selected.items():
        appended = f"raw/{path}.json"
        if appended in members:
            result[file_id] = appended
            continue
        candidates = [appended]
        if path.endswith(".json"):
            unchanged = f"raw/{path}"
            candidates.append(unchanged)
            if unchanged in members:
                # The same member may be another file's normal appended-suffix export,
                # including an inventory file outside this selection.
                if path[:-5] in inventory:
                    raise ValueError(f"压缩包原始词条文件归属不明确：{unchanged}；本轮未采用压缩包。")
                result[file_id] = unchanged
                continue
        raise ValueError(
            f"新压缩包缺少所选文件的原始词条文件：{path}（已查找：{'、'.join(candidates)}）；本轮未采用压缩包。"
        )
    return result


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
