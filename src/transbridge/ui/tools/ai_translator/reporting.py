"""Canonical AI translation report projection and artifact rendering."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from transbridge.application.translation import (
    ReportSnapshot,
    render_report_bundle,
)
from transbridge.paratranz.config_manager import LLMConfig

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TranslationReportArtifacts:
    paths: tuple[str, ...]
    excel_path: str | None
    diagnostics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TranslationReportOverview:
    errors: int
    warnings: int
    changed: int
    needs_review: int


def report_overview(snapshot: ReportSnapshot | None) -> TranslationReportOverview:
    if snapshot is None:
        return TranslationReportOverview(0, 0, 0, 0)
    errors = sum(1 for item in snapshot.diagnostics if item.severity.value == "error")
    warnings = sum(1 for item in snapshot.diagnostics if item.severity.value == "warning")
    changed = sum(1 for item in snapshot.candidates if item.text != item.before_text)
    needs_review = sum(1 for item in snapshot.candidates if not item.accepted)
    return TranslationReportOverview(errors, warnings, changed, needs_review)


def render_translation_report(snapshot: ReportSnapshot | None, esp_stem: str) -> TranslationReportArtifacts:
    """Render JSON/CSV/Excel from one snapshot and retain 20 runs per format."""
    return render_snapshot_report(snapshot, esp_stem)


def render_snapshot_report(snapshot: ReportSnapshot | None, esp_stem: str) -> TranslationReportArtifacts:
    """Render every canonical report source through the shared bundle renderer."""
    if snapshot is None:
        return TranslationReportArtifacts((), None, ())
    output_dir = Path(LLMConfig.get_ai_translator_dir(esp_stem)) / "reports"
    rendered = render_report_bundle(
        snapshot,
        base_dir=output_dir,
        max_files_per_format=20,
    )
    artifacts = rendered.value.artifacts if rendered.value is not None else ()
    paths = tuple(item.artifact_path for item in artifacts if item.artifact_path)
    excel_path = next(
        (item.artifact_path for item in artifacts if item.renderer == "excel" and item.artifact_path),
        None,
    )
    diagnostics = tuple(f"{item.code}: {item.message}" for item in rendered.diagnostics)
    for diagnostic in diagnostics:
        _logger.warning("AI report renderer diagnostic: %s", diagnostic)
    return TranslationReportArtifacts(paths, excel_path, diagnostics)


def diagnostic_entry_id(details: tuple[tuple[str, object], ...]) -> str:
    entry_key = dict(details).get("entry_key")
    if isinstance(entry_key, dict):
        return str(entry_key.get("local_key", ""))
    return ""


__all__ = [
    "TranslationReportArtifacts",
    "TranslationReportOverview",
    "diagnostic_entry_id",
    "render_snapshot_report",
    "render_translation_report",
    "report_overview",
]
