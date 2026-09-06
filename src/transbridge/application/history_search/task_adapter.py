"""TaskRuntime entrypoint for non-blocking history index refreshes."""

from __future__ import annotations

from threading import Lock

from transbridge.application.contracts import Deferred, JobRef
from transbridge.application.tasks import JobCapabilities, JobSpec, OwnerRef, TaskRuntime

from .models import RefreshReport
from .service import HistorySearchRefreshService


class HistorySearchTaskEntrypoint:
    def __init__(self, runtime: TaskRuntime, refresh_service: HistorySearchRefreshService) -> None:
        self.runtime = runtime
        self._refresh_service = refresh_service
        self._lock = Lock()
        self._reports: dict[str, RefreshReport] = {}

    def refresh(self, owner: OwnerRef) -> Deferred[JobRef]:
        spec = JobSpec(
            job_type="history-search.refresh",
            input_ref="local-persisted-history",
            input_fingerprint="history-search-index-v1",
            display_name="刷新历史翻译与术语索引",
            capabilities=JobCapabilities(supports_cancel=True),
        )
        deferred = self.runtime.submit(spec, owner)

        def workload(cancellation) -> None:
            report = self._refresh_service.refresh(cancellation)
            with self._lock:
                self._reports[deferred.ref.run_id] = report
                while len(self._reports) > 32:
                    self._reports.pop(next(iter(self._reports)))
            self.runtime.update_progress(
                deferred.ref,
                owner,
                {"records": report.record_count, "diagnostics": len(report.diagnostics)},
            )

        self.runtime.schedule(deferred.ref, owner, workload)
        return deferred

    def report(self, run_id: str) -> RefreshReport | None:
        with self._lock:
            return self._reports.get(run_id)
