# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read and update references for saved and pending project sources."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .source_content import registered_source_ids
from .storage_contracts import SourceInfo, validate_source_reference_path
from .transition_preparation import ActiveSignature, active_signature

if TYPE_CHECKING:
    from transbridge.application.io.identity import SourceNamespace
    from transbridge.persistence.v2.ids import ProjectId, VariantId

    from .lifecycle import ProjectLifecycleService
    from .models import ActiveProject


@dataclass(frozen=True, slots=True)
class PendingSourceReferences:
    signature: ActiveSignature
    generation: int
    sources: tuple[SourceInfo, ...]


@dataclass(frozen=True, slots=True)
class PendingSourceReferenceUpdate:
    capture: PendingSourceReferences
    source_id: str
    namespace: SourceNamespace
    expected_fingerprint: str
    expected_path: str
    reference_path: str


class ProjectSourceReferences:
    def __init__(self, projects, lifecycle: ProjectLifecycleService) -> None:
        self.store_for = projects.store_for
        self.update_source_reference = projects.update_source_reference
        self._lifecycle = lifecycle

    def capture_pending_sources(
        self, project_id: ProjectId, variant_id: VariantId, *, project_revision: int, variant_revision: int
    ) -> PendingSourceReferences | None:
        signature = project_id.value, project_revision, variant_id.value, variant_revision
        return self._lifecycle.capture_pending_source_references(signature)

    def update_pending_source_reference(
        self,
        capture: PendingSourceReferences,
        source_id: str,
        namespace: SourceNamespace,
        *,
        expected_fingerprint: str,
        expected_path: str,
        reference_path: str,
    ) -> bool:
        update = PendingSourceReferenceUpdate(
            capture, source_id, namespace, expected_fingerprint, expected_path, reference_path
        )
        return self._lifecycle.update_pending_source_reference(update)


def capture_pending_references(
    active: ActiveProject, generation: int, signature: ActiveSignature
) -> PendingSourceReferences | None:
    if active_signature(active) != signature:
        return None
    registered = registered_source_ids(active.project)
    sources = {
        source.namespace: source.info for source in active.source_additions if source.namespace.value in registered
    }
    return PendingSourceReferences(signature, generation, tuple(sources.values()))


def update_pending_reference(
    active: ActiveProject, generation: int, update: PendingSourceReferenceUpdate
) -> ActiveProject | None:
    capture, namespace = update.capture, update.namespace
    if capture.generation != generation or capture.signature != active_signature(active):
        return None
    if registered_source_ids(active.project).get(namespace.value) != update.source_id:
        return None
    captured = next((item for item in capture.sources if item.namespace == namespace), None)
    if (
        captured is None
        or captured.fingerprint != update.expected_fingerprint
        or captured.reference_path != update.expected_path
    ):
        return None
    additions = active.source_additions
    index = next((i for i in range(len(additions) - 1, -1, -1) if additions[i].namespace == namespace), None)
    if index is None:
        return None
    source = additions[index]
    if source.fingerprint != update.expected_fingerprint or source.reference_path != update.expected_path:
        return None
    if update.reference_path == update.expected_path:
        return active
    validate_source_reference_path(update.reference_path)
    updated = replace(source, reference_path=update.reference_path)
    return replace(active, source_additions=(*additions[:index], updated, *additions[index + 1 :]))
