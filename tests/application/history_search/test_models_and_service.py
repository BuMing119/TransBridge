from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.contracts import Deferred, JobRef
from transbridge.application.history_search import (
    HistoryDiagnostic,
    HistoryEntryKind,
    HistorySearchRefreshService,
    HistorySearchTaskEntrypoint,
    HistorySourceRef,
    HistorySourceType,
    ProviderResult,
    RefreshReport,
    SourceRecord,
    normalize_search_text,
)
from transbridge.application.tasks import OwnerRef


def _record(text: str) -> SourceRecord:
    return SourceRecord(
        HistoryEntryKind.TRANSLATION,
        text,
        "译文",
        HistorySourceRef(HistorySourceType.DICTIONARY, text, text),
    )


def test_normalize_search_text_handles_unicode_newlines_whitespace_and_case() -> None:
    assert normalize_search_text("  ＳＫＹＲＩＭ\r\nCoast  ") == "skyrim\ncoast"


def test_refresh_isolates_provider_failure_and_publishes_one_complete_snapshot() -> None:
    @dataclass
    class Provider:
        name: str
        result: ProviderResult | None = None

        def collect(self, _cancellation):
            if self.result is None:
                raise OSError("broken")
            return self.result

    class Index:
        captured = None

        def replace(self, records, diagnostics, *, built_at, cancellation=None):
            self.captured = (records, diagnostics, built_at, cancellation)

    index = Index()
    service = HistorySearchRefreshService(
        index,
        (
            Provider("projects", ProviderResult((_record("Skyrim"),))),
            Provider("dictionaries"),
            Provider("terms", ProviderResult(diagnostics=(HistoryDiagnostic("WARN", "warning"),))),
        ),
    )

    report = service.refresh()

    assert report.record_count == 1
    assert report.provider_count == 3
    assert {item.code for item in report.diagnostics} == {"HISTORY_PROVIDER_FAILED", "WARN"}
    assert index.captured[0] == (_record("Skyrim"),)


def test_task_entrypoint_keeps_report_by_run_and_updates_runtime_progress() -> None:
    report = RefreshReport(4, 3, "now")

    class Refresh:
        def refresh(self, _cancellation):
            return report

    class Runtime:
        progress = None

        def submit(self, specification, owner):
            self.specification = specification
            self.owner = owner
            return Deferred(JobRef("job", owner.owner_id, "run"))

        def schedule(self, ref, owner, workload):
            workload(object())

        def update_progress(self, ref, owner, progress):
            self.progress = progress

    runtime = Runtime()
    entrypoint = HistorySearchTaskEntrypoint(runtime, Refresh())

    deferred = entrypoint.refresh(OwnerRef("owner", "history-search"))

    assert deferred.ref.run_id == "run"
    assert entrypoint.report("run") is report
    assert runtime.progress == {"records": 4, "diagnostics": 0}
    assert runtime.specification.job_type == "history-search.refresh"
