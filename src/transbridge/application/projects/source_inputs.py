"""Read-only availability of source input, independent of saved translation state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, ErrorCategory
from transbridge.application.io import FormatId

from .source_content import authoritative_baseline_sources, source_content_identity

if TYPE_CHECKING:
    from transbridge.persistence.v2.models import ProjectDto
    from transbridge.persistence.v2.variant import VariantSnapshot

    from .provisioning_models import PreparedSourceHydration


@dataclass(frozen=True, slots=True)
class MissingProjectSourceInput:
    source_id: str | None
    namespace: str | None
    display_name: str
    format_id: FormatId | None
    diagnostic: Diagnostic

    def to_dict(self):
        return {
            "source_id": self.source_id,
            "namespace": self.namespace,
            "display_name": self.display_name,
            "format_id": None if self.format_id is None else self.format_id.value,
            "diagnostic": self.diagnostic.to_dict(),
        }


def missing_inputs_for(
    project: ProjectDto,
    snapshot: VariantSnapshot,
    hydrations: tuple[PreparedSourceHydration, ...],
    diagnostics: Mapping[str, Diagnostic] | None = None,
) -> tuple[MissingProjectSourceInput, ...]:
    """Describe uncovered saved identities without manufacturing source content."""
    diagnostics = diagnostics or {}
    sources = authoritative_baseline_sources(
        tuple(project.envelope.data.get("sources", ())), tuple(project.envelope.data.get("source_relations", ()))
    )
    keys = {entry.entry_key for hydration in hydrations for entry in hydration.entries}
    available_namespaces = {key.namespace.value for key in keys}
    available_ids = {item.source_id for item in hydrations if item.source_id}
    fingerprints = {(item.fingerprint, item.format_id.value) for item in hydrations if not item.source_id}
    resolved = []
    for source in sources:
        namespace = source_content_identity(source)
        if namespace is None and source.get("fingerprint") is not None:
            matches = {
                item.namespace.value for item in snapshot.source_fingerprints if item.sha256 == source["fingerprint"]
            }
            namespace = next(iter(matches)) if len(matches) == 1 else None
        available = (
            source.get("source_id") in available_ids
            or (source.get("fingerprint"), source.get("format_id")) in fingerprints
        )
        if available and namespace is not None:
            available_namespaces.add(namespace)
        resolved.append((source, namespace, available))
    uncovered = {
        entry.entry_key.namespace.value
        for entry in snapshot.entries
        if not entry.tombstone and entry.entry_key not in keys
    }
    missing_namespaces = (
        {item.namespace.value for item in snapshot.source_fingerprints} - available_namespaces
    ) | uncovered
    missing = []
    assigned = set()
    for source, namespace, available in resolved:
        if available and namespace not in missing_namespaces:
            continue
        source_id = source.get("source_id")
        diagnostic = diagnostics.get(source_id) or diagnostics.get(namespace) or _missing_diagnostic()
        missing.append(
            MissingProjectSourceInput(
                source_id,
                namespace,
                _source_label(source, namespace),
                FormatId(source["format_id"]) if source.get("format_id") else None,
                diagnostic,
            )
        )
        if namespace is not None:
            assigned.add(namespace)
    for namespace in sorted(missing_namespaces - assigned):
        missing.append(
            MissingProjectSourceInput(
                None, namespace, f"来源内容 · {namespace}", None, diagnostics.get(namespace) or _missing_diagnostic()
            )
        )
    return tuple(missing)


def _source_label(source, namespace):
    # A saved reference supplies a name only, including when copied from another
    # computer. Never probe or reopen it to describe the missing input.
    legacy = source.get("legacy") or {}
    reference = source.get("location") or source.get("path") or legacy.get("path")
    name = PureWindowsPath(str(reference)).name if reference else None
    return str(source.get("display_name") or name or source.get("source_id") or namespace or "来源内容待补充")


def _missing_diagnostic():
    return Diagnostic(
        "SOURCE_BASELINE_REQUIRED",
        "该来源的保存内容尚不可用；已有译文可以继续编辑和保存，需要原文或文件内容时请补充输入。",
        DiagnosticSeverity.WARNING,
        ErrorCategory.PREREQUISITE,
    )
