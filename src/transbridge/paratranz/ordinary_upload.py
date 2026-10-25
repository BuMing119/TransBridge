"""File-scoped ordinary upload while retaining the reviewed sync-plan protocol."""

from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.converter.translation_entry import TranslationEntry

from .sync_snapshot import _snapshot


class OrdinaryUploadAdapter:
    """Only the fixed root path supplies candidates or receives mutations."""

    def __init__(self, service, gateway, path: str) -> None:
        if not path or "/" in path or "\\" in path or not path.endswith(".json"):
            raise ValueError("普通上传需要固定根目录 JSON 文件名。")
        self.service = service
        self.gateway = gateway
        self.path = path
        self.file_id = None
        self._entries = {}

    def fetch(self, project_id, namespace, *, limit, cancellation=None):
        self._cancel(cancellation)
        files = self.gateway.find_files(project_id, self.path)
        if len(files) > 1:
            raise ValueError("普通上传根目录路径存在多个文件，无法安全选择目标。")
        file = files[0] if files else None
        if file is not None and file.path != self.path:
            raise ValueError("云端返回了普通上传目标路径以外的文件。")
        entries = () if file is None else file.entries
        if len(entries) > limit:
            raise ValueError("remote snapshot exceeds the configured complete-plan limit")
        if len({entry.remote_id for entry in entries}) != len(entries):
            raise ValueError("普通上传文件返回重复词条身份。")
        self.file_id = None if file is None else file.file_id
        if any(entry.file_id != self.file_id for entry in entries):
            raise ValueError("普通上传文件返回其他文件的词条。")
        self._entries = {entry.remote_id: entry for entry in entries}
        self._cancel(cancellation)
        return tuple(_snapshot(_typed(entry), namespace, f"project:{project_id}") for entry in entries)

    def upsert_entry(self, project_id, entry, *, force_overwrite=False, cancellation=None):
        self._cancel(cancellation)
        if force_overwrite:
            raise ValueError("普通上传只允许更新已审阅词条的译文和状态。")
        local = TranslationEntry(entry.key, entry.key, entry.original, entry.translation, entry.stage, entry.context)
        if self.file_id is None:
            if self.gateway.find_files(project_id, self.path):
                raise RuntimeError("普通上传目标已出现，请重新检查当前文件。")
            # A real record creates the file; ParaTranz rejects empty JSON imports.
            created = self.gateway.create_file(project_id, self.path, (local,))
            if created.path != self.path or len(created.entries) != 1:
                raise ValueError("云端新建文件未确认普通上传路径和内容。")
            self.file_id = created.file_id
            remote = created.entries[0]
        else:
            self._check_file(project_id)
            remote = self.gateway.create_entry(project_id, self.file_id, local)
        if remote.remote_id in self._entries or (remote.file_id, remote.key, remote.original, remote.context) != (
            self.file_id,
            entry.key,
            entry.original,
            entry.context,
        ):
            raise ValueError("云端新建词条不属于普通上传目标。")
        self._entries[remote.remote_id] = remote
        if (remote.translation, remote.stage) != (entry.translation, entry.stage):
            self._check_entry(project_id, remote.remote_id)
            remote = self.gateway.update_translation(project_id, remote.remote_id, entry.translation, entry.stage)
            self._entries[remote.remote_id] = remote
        if (remote.file_id, remote.key, remote.original, remote.context, remote.translation, remote.stage) != (
            self.file_id,
            entry.key,
            entry.original,
            entry.context,
            entry.translation,
            entry.stage,
        ):
            raise ValueError("云端新建内容尚未确认译文和状态，请重新检查后上传。")
        self._cancel(cancellation)
        return _typed(remote)

    def update_entry_translation(self, project_id, entry, *, cancellation=None):
        self._cancel(cancellation)
        self._check_entry(project_id, entry.remote_id)
        result = self.gateway.update_translation(project_id, entry.remote_id, entry.translation, entry.stage)
        if result.file_id != self.file_id:
            raise ValueError("云端更新结果已离开普通上传目标文件。")
        self._entries[result.remote_id] = result
        return _typed(result)

    def delete_entry(self, project_id, remote_id, *, cancellation=None):
        self._cancel(cancellation)
        self._check_entry(project_id, remote_id)
        self.service.delete_entry(project_id, remote_id, cancellation=cancellation)
        self._entries.pop(remote_id, None)

    def close(self):
        try:
            self.gateway.close()
        finally:
            self.service.close()

    def _check_file(self, project_id):
        if self.file_id is None or self.gateway.read_file_path(project_id, self.file_id) != self.path:
            raise RuntimeError("普通上传目标已移动或删除，请重新检查当前文件。")

    def _check_entry(self, project_id, remote_id):
        current = self.gateway.read_entry(project_id, remote_id)
        if current is None or current.file_id != self.file_id or current != self._entries.get(remote_id):
            raise RuntimeError("普通上传词条已变化或离开目标文件，未执行写入。")
        self._check_file(project_id)

    def _cancel(self, cancellation):
        if cancellation is not None:
            self.gateway.cancellation = cancellation
            cancellation.raise_if_cancelled()


def _typed(entry):
    return ParaTranzEntry(
        entry.remote_id, entry.key, entry.original, entry.translation, entry.context, entry.stage, entry.file_id
    )
