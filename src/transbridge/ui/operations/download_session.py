"""Keep one complete cloud read for the lifetime of a download review."""

from threading import RLock

from transbridge.application.sync.reviewed_snapshot import ReviewedDownloadSnapshot

from .categorized_download_scope import download_snapshot_adapter


def download_session_scope(request) -> tuple:
    return (
        request.project_id,
        request.namespace,
        request.target_endpoint,
        request.target_account_user_id,
        request.target_status,
        request.config_revision,
        request.active_version_identity,
        request.project_revision,
        request.variant_revision,
        request.upload_path,
        request.local_entries,
    )


class DownloadSession:
    """Policy/choice edits reuse data; another target or local revision cannot."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._scope: tuple | None = None
        self._snapshot: ReviewedDownloadSnapshot | None = None
        self._project_name = ""

    def load(self, service, request) -> tuple[ReviewedDownloadSnapshot, str]:
        scope = download_session_scope(request)
        with self._lock:
            if self._scope is not None and self._scope != scope:
                raise ValueError("download review belongs to another target or local revision")
            if self._snapshot is None:
                if request.target_account_user_id is None:
                    raise PermissionError("当前 ParaTranz 账号尚未验证")
                projects = service.list_projects(uid=request.target_account_user_id)
                project = next((item for item in projects if item.project_id == request.project_id), None)
                if project is None:
                    name = request.target_project_name or "所选 ParaTranz 项目"
                    raise PermissionError(f"当前账号无权访问“{name}”")
                entries = download_snapshot_adapter(service, request).fetch(
                    request.project_id, request.namespace, limit=100_000
                )
                # Publish only a complete read. A failed read is retried in full.
                self._snapshot = ReviewedDownloadSnapshot(request.project_id, request.namespace, entries)
                self._project_name = project.name
                self._scope = scope
            return self._snapshot, self._project_name
