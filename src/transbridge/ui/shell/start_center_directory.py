# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Load start-center project and task directories outside the Qt event loop."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from transbridge.application.projects import ProjectCatalogEntry
from transbridge.application.tasks import OwnerRef
from transbridge.ui.workers import ApiWorker

from .start_center import RecentProjectViewState, RecoveryItemViewState

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StartCenterDirectorySnapshot:
    projects: tuple[ProjectCatalogEntry, ...] = ()
    recovery: tuple[RecoveryItemViewState, ...] = ()
    diagnostic: str = ""

    def project_rows(self, active_project_id: str | None) -> tuple[RecentProjectViewState, ...]:
        # Another GUI process can change the shared startup pointer without
        # changing this window's workbench.
        return tuple(
            RecentProjectViewState(
                project_key=item.project_id,
                name=item.name,
                path=item.path,
                available=item.available,
                reason=item.reason or "",
                active=item.project_id == active_project_id,
            )
            for item in self.projects
        )


def read_start_center_directory(use_cases, context) -> StartCenterDirectorySnapshot:
    projects = ()
    recovery = ()
    diagnostics = []
    try:
        if "project_catalog" in use_cases.names():
            catalog = use_cases.resolve("project_catalog").list_projects()
            projects = catalog.projects
            diagnostics.extend(f"{item.code}: {item.message}" for item in catalog.diagnostics)
    except Exception:
        logger.exception("Failed to read the start center project catalog")
        diagnostics.append("工程目录暂时不可用。")
    if context is not None:
        try:
            actor = OwnerRef(
                owner_id=context.owner_id,
                entrypoint=dict(context.metadata).get("entrypoint", "gui"),
                project_id=context.project_id,
                variant_id=context.variant_id,
                session_id=context.session_id,
                permissions=context.permissions,
            )
            recovery = tuple(
                RecoveryItemViewState(
                    storage_key=item.storage_key,
                    title=item.display_name,
                    recoverable=item.recoverable,
                    reason=item.reason_message or item.reason_code,
                    run_id=item.run_id,
                )
                for item in use_cases.resolve("task_recovery").list(actor)
            )
        except Exception:
            logger.exception("Failed to project recoverable tasks for the start center")
            diagnostics.append("任务恢复状态暂时不可用。")
    return StartCenterDirectorySnapshot(projects, recovery, "\n".join(diagnostics))


class StartCenterDirectoryLoader:
    """Own one registered worker and coalesce refresh requests to the latest revision."""

    def __init__(self, on_snapshot, *, is_closing) -> None:
        self._on_snapshot = on_snapshot
        self._is_closing = is_closing
        self._worker: ApiWorker | None = None
        self._pending = None

    def request(self, revision: int, use_cases, context) -> None:
        self._pending = revision, use_cases, context
        self._start_pending()

    def invalidate(self) -> None:
        self._pending = None

    def _start_pending(self) -> None:
        if self._worker is not None or self._pending is None or self._is_closing():
            return
        revision, use_cases, context = self._pending
        self._pending = None
        try:
            worker = ApiWorker(read_start_center_directory, use_cases, context, route_http_errors=False)
            self._worker = worker
            worker.result.connect(lambda value: self._deliver(revision, value))
            worker.error.connect(lambda error: self._failed(revision, error))
            worker.finished.connect(self._finished)
            worker.start()
        except Exception as exc:
            self._worker = None
            logger.exception("Failed to start the start center directory reader")
            self._failed(revision, str(exc))

    def _deliver(self, revision: int, snapshot: StartCenterDirectorySnapshot) -> None:
        if not self._is_closing():
            self._on_snapshot(revision, snapshot)

    def _failed(self, revision: int, error: str) -> None:
        self._deliver(revision, StartCenterDirectorySnapshot(diagnostic=f"开始页目录暂时不可用：{error}"))

    def _finished(self) -> None:
        self._worker = None
        self._start_pending()
