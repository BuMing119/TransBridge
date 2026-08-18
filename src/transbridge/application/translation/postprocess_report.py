"""Canonical report renderers for ReportSnapshot.

UI, Excel and durable history all consume the same ReportSnapshot; a renderer
is a pure projection.  Renderer failures never roll back already-committed
business: :func:`render_report` returns a COMPLETED operation result carrying
a REPORT_RENDER_FAILED diagnostic and failed counts instead of raising.
Renderers receive only the snapshot — prompts, credentials and model messages
never enter report content.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
from typing import Protocol

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)

from .postprocess import ReportSnapshot


@dataclass(frozen=True, slots=True)
class ReportRenderResult:
    renderer: str
    size: int
    sha256: str
    artifact_path: str | None = None
    content: bytes = b""

    @property
    def fingerprint(self) -> str:
        return self.sha256


class ReportRendererPort(Protocol):
    name: str

    def render(self, snapshot: ReportSnapshot, *, base_dir: Path | None = None) -> ReportRenderResult: ...


@dataclass(frozen=True, slots=True)
class ReportRenderOutcome:
    artifacts: tuple[ReportRenderResult, ...]
    produced: int
    failed: int
    run_id: str


class JsonReportRenderer:
    name = "json"

    def render(self, snapshot: ReportSnapshot, *, base_dir: Path | None = None) -> ReportRenderResult:
        content = json.dumps(
            snapshot.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return ReportRenderResult(
            self.name,
            len(content),
            _sha256(content),
            artifact_path=_artifact(base_dir, "postprocess-report", "json", content),
            content=content,
        )


class CsvReportRenderer:
    name = "csv"

    def render(self, snapshot: ReportSnapshot, *, base_dir: Path | None = None) -> ReportRenderResult:
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            (
                "schema",
                "run_id",
                "outcome",
                "namespace",
                "local_key",
                "stage",
                "accepted",
                "before",
                "candidate",
            )
        )
        for candidate in snapshot.candidates:
            key = candidate.entry_key.to_dict()
            writer.writerow(
                (
                    snapshot.schema,
                    snapshot.run_id,
                    snapshot.outcome.value,
                    key.get("namespace", ""),
                    key.get("local_key", ""),
                    candidate.stage,
                    "1" if candidate.accepted else "0",
                    candidate.before_text,
                    candidate.text,
                )
            )
        content = stream.getvalue().encode("utf-8")
        return ReportRenderResult(
            self.name,
            len(content),
            _sha256(content),
            artifact_path=_artifact(base_dir, "postprocess-report", "csv", content),
            content=content,
        )


class ExcelReportRenderer:
    """Additive renderer; keeps the canonical snapshot as the only source."""

    name = "excel"

    def render(self, snapshot: ReportSnapshot, *, base_dir: Path | None = None) -> ReportRenderResult:
        try:
            from openpyxl import Workbook  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - degraded environment probe
            raise RuntimeError("openpyxl is unavailable; Excel rendering is degraded") from exc

        workbook = Workbook()
        summary = workbook.active
        summary.title = "Summary"
        summary.append(("schema", snapshot.schema))
        summary.append(("run_id", snapshot.run_id))
        summary.append(("outcome", snapshot.outcome.value))
        summary.append(("input_count", snapshot.input_count))
        summary.append(("accepted_count", snapshot.accepted_count))
        summary.append(("issue_count", snapshot.issue_count))
        summary.append(("failure_count", snapshot.failure_count))

        entries = workbook.create_sheet("Entries")
        entries.append(("namespace", "local_key", "stage", "accepted", "before", "candidate"))
        for candidate in snapshot.candidates:
            key = candidate.entry_key.to_dict()
            entries.append(
                (
                    key.get("namespace", ""),
                    key.get("local_key", ""),
                    candidate.stage,
                    candidate.accepted,
                    candidate.before_text,
                    candidate.text,
                )
            )

        stream = io.BytesIO()
        workbook.save(stream)
        content = stream.getvalue()
        return ReportRenderResult(
            self.name,
            len(content),
            _sha256(content),
            artifact_path=_artifact(base_dir, "postprocess-report", "xlsx", content),
            content=content,
        )


def _artifact(base_dir: Path | None, stem: str, suffix: str, content: bytes) -> str | None:
    if base_dir is None:
        return None
    base_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()[:16]
    target = base_dir / f"{stem}-{digest}.{suffix}"
    target.write_bytes(content)
    return str(target)


def default_report_renderers() -> tuple[ReportRendererPort, ...]:
    return (JsonReportRenderer(), CsvReportRenderer())


def render_report(
    snapshot: ReportSnapshot,
    renderers: tuple[ReportRendererPort, ...] | None = None,
    *,
    base_dir: Path | None = None,
) -> OperationResult[ReportRenderOutcome]:
    """Render all projections; never roll back committed business.

    A renderer exception is recorded as a REPORT_RENDER_FAILED diagnostic and
    a failed count while the operation outcome stays COMPLETED.
    """
    renderers = renderers or default_report_renderers()
    artifacts: list[ReportRenderResult] = []
    diagnostics: list[Diagnostic] = []
    failed = 0
    for renderer in renderers:
        try:
            artifacts.append(renderer.render(snapshot, base_dir=base_dir))
        except Exception as exc:
            failed += 1
            diagnostics.append(
                Diagnostic(
                    "REPORT_RENDER_FAILED",
                    "A report renderer failed; committed results are unaffected.",
                    category=ErrorCategory.INTERNAL,
                    severity=DiagnosticSeverity.ERROR,
                    details=(
                        ("renderer", renderer.name),
                        ("error_type", type(exc).__name__),
                    ),
                )
            )
    value = ReportRenderOutcome(tuple(artifacts), len(artifacts), failed, snapshot.run_id)
    if failed == 0:
        outcome = OperationOutcome.COMPLETED
        counts = OperationCounts(succeeded=len(artifacts))
        return OperationResult(outcome, value, counts=counts, run_id=snapshot.run_id)
    if artifacts:
        outcome = OperationOutcome.PARTIAL
        counts = OperationCounts(succeeded=len(artifacts), failed=failed)
        return OperationResult(
            outcome, value, diagnostics=tuple(diagnostics), counts=counts, run_id=snapshot.run_id
        )
    outcome = OperationOutcome.FAILED
    counts = OperationCounts(failed=failed)
    return OperationResult(outcome, None, diagnostics=tuple(diagnostics), counts=counts, run_id=snapshot.run_id)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()