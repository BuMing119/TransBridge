"""Context-scoped durable facade for inbound terminology review and draft import."""

from __future__ import annotations

from typing import Protocol

from transbridge.application.contracts import RequestContext

from .draft_import_models import DraftImportCommitResult, DraftImportProposal, DraftImportSelection
from .inbound import InboundTerminologyChangeSet


class InboundChangeSetQueryPort(Protocol):
    def list_change_sets(self, project_id: str, variant_id: str) -> tuple[InboundTerminologyChangeSet, ...]: ...

    def get_change_set(self, change_set_id: str) -> InboundTerminologyChangeSet: ...


class InboundDraftImportPort(Protocol):
    def preview(self, selection: DraftImportSelection) -> DraftImportProposal: ...

    def commit(self, proposal: DraftImportProposal, context: RequestContext) -> DraftImportCommitResult: ...


class DurableTerminologyInboundService:
    """Bind durable inbound queries to the explicit draft preview/commit workflow."""

    def __init__(self, store: InboundChangeSetQueryPort, importer: InboundDraftImportPort) -> None:
        self._store = store
        self._importer = importer

    def list_inbound(self, context: RequestContext) -> tuple[InboundTerminologyChangeSet, ...]:
        project_id, variant_id = _scope(context)
        return self._store.list_change_sets(project_id, variant_id)

    def get_inbound(self, context: RequestContext, change_set_id: str) -> InboundTerminologyChangeSet:
        project_id, variant_id = _scope(context)
        change_set = self._store.get_change_set(change_set_id)
        if (change_set.project_id, change_set.variant_id) != (project_id, variant_id):
            raise PermissionError("inbound terminology change set belongs to another Project/Variant")
        return change_set

    def preview_import(self, selection: DraftImportSelection) -> DraftImportProposal:
        line = selection.expected_line
        change_set = self._store.get_change_set(selection.change_set_id)
        if (change_set.project_id, change_set.variant_id) != (line.project_id, line.variant_id):
            raise PermissionError("draft import selection belongs to another Project/Variant")
        if selection.change_set_content_digest != change_set.content_digest:
            raise ValueError("draft import selection does not match the durable change set")
        return self._importer.preview(selection)

    def commit_import(self, proposal: DraftImportProposal, context: RequestContext) -> DraftImportCommitResult:
        self.get_inbound(context, proposal.selection.change_set_id)
        return self._importer.commit(proposal, context)


def _scope(context: RequestContext) -> tuple[str, str]:
    if context.project_id is None or context.variant_id is None:
        raise ValueError("inbound terminology review requires Project and Variant context")
    return context.project_id, context.variant_id


__all__ = [
    "DurableTerminologyInboundService",
    "InboundChangeSetQueryPort",
    "InboundDraftImportPort",
]
