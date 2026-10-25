"""In-memory file endpoint behind the real current-path download adapter."""

from transbridge.paratranz import path_snapshot


class MemoryCurrentDownloadGateway:
    def __init__(self, service, *, path="source.esp.json"):
        self.service = service
        self.path = path
        self.reads = []
        self.closed = 0

    def inventory(self, project_id):
        self.reads.append(("inventory", project_id))
        return {self.path: 11}

    def get_file_translation(self, project_id, file_id):
        assert file_id == 11
        self.reads.append(("translation", project_id, file_id))
        # list_entries is solely this fake endpoint's dynamic data provider.
        # Production reads must still traverse CurrentPathSnapshotAdapter.
        return [
            dict(entry.to_remote_payload(), id=entry.remote_id, fileId=entry.file_id or file_id)
            for entry in self.service.list_entries(project_id, limit=100_001)
        ]

    def close(self):
        self.closed += 1


def patch_current_download_gateway(monkeypatch, service, **kwargs):
    gateway = MemoryCurrentDownloadGateway(service, **kwargs)

    def factory(_config):
        return gateway

    monkeypatch.setattr(path_snapshot, "FilePublicationGateway", factory)
    return gateway
