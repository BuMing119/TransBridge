"""Stable renderer results and changelog semantic parity manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import ChangeLogDocumentManifest


@dataclass(frozen=True, slots=True)
class SemanticManifest:
    fact_ref: str
    fact_digest: str
    facts_digest: str
    messages_digest: str
    typed_changes_digest: str
    message_count: int
    change_count: int
    diagnostic_count: int


@dataclass(frozen=True, slots=True)
class RenderedArtifact:
    format: str
    renderer_version: str
    path: Path
    size: int
    sha256: str
    semantic_manifest: SemanticManifest
    sheet_names: tuple[str, ...] = ()


def changelog_semantic_manifest(document: ChangeLogDocumentManifest) -> SemanticManifest:
    return SemanticManifest(
        document.ref.document_id,
        document.ref.content_digest,
        document.ref.content_digest,
        document.section_digest("messages"),
        document.section_digest("changes"),
        document.section_count("messages"),
        document.section_count("changes"),
        document.section_count("diagnostics"),
    )


__all__ = ["RenderedArtifact", "SemanticManifest", "changelog_semantic_manifest"]
