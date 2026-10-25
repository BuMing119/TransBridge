"""In-memory file boundary for production ordinary-upload integration tests."""

from dataclasses import replace

from transbridge.application.categorized_upload import RemoteEntry, RemoteFile
from transbridge.application.ports.paratranz import ParaTranzEntry


class MemoryOrdinaryGateway:
    def __init__(self, service, *, path="source.esp.json"):
        self.service = service
        self.files = {11: path}
        self.created_files = []
        self.reads = []

    def _entries(self, project_id):
        return tuple(
            RemoteEntry(
                item.remote_id,
                item.file_id or 11,
                item.key,
                item.original,
                item.translation,
                item.context,
                item.stage,
            )
            for item in self.service.list_entries(project_id, limit=100_001)
        )

    def find_files(self, project_id, path):
        self.reads.append(("files", path))
        entries = self._entries(project_id)
        return tuple(
            RemoteFile(file_id, name, tuple(item for item in entries if item.file_id == file_id))
            for file_id, name in self.files.items()
            if name == path
        )

    def read_file_path(self, project_id, file_id):
        self.reads.append(("path", file_id))
        return self.files.get(file_id)

    def read_entry(self, project_id, remote_id):
        self.reads.append(("entry", remote_id))
        return next((entry for entry in self._entries(project_id) if entry.remote_id == remote_id), None)

    def create_file(self, project_id, path, entries):
        assert entries
        assert path not in self.files.values()
        file_id = max(self.files, default=10) + 1
        self.files[file_id] = path
        self.created_files.append((file_id, path, entries))
        created = tuple(self.create_entry(project_id, file_id, entry) for entry in entries)
        return RemoteFile(file_id, path, created)

    def create_entry(self, project_id, file_id, entry):
        payload = {
            "fileId": file_id,
            "key": entry.key,
            "original": entry.original,
            "translation": entry.translation or "",
            "context": entry.context or "",
            "stage": entry.stage,
        }
        result = self.service._strings.create_string(project_id, payload)
        return self.read_entry(project_id, result["id"])

    def update_translation(self, project_id, remote_id, translation, stage):
        before = self.read_entry(project_id, remote_id)
        self.service.update_entry_translation(
            project_id,
            ParaTranzEntry(remote_id, before.key, before.original, translation, before.context, stage, before.file_id),
        )
        return replace(before, translation=translation, stage=stage)

    def close(self):
        pass


def patch_ordinary_gateway(monkeypatch, service, **kwargs):
    gateway = MemoryOrdinaryGateway(service, **kwargs)
    monkeypatch.setattr(
        "transbridge.ui.operations.ordinary_upload_scope.CategorizedUploadGateway", lambda _config: gateway
    )
    return gateway
