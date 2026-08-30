from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.drafts import DraftLineState
from transbridge.application.terminology_sync.draft_import_models import (
    DraftImportChoice,
    DraftImportCommitResult,
    DraftImportProposal,
    DraftImportSelection,
)
from transbridge.application.terminology_sync.inbound import (
    InboundReviewDecision,
    InboundTerminologyChangeSet,
)
from transbridge.application.terminology_sync.inbound_service import DurableTerminologyInboundService


class _Store:
    def __init__(self, change_set: InboundTerminologyChangeSet) -> None:
        self.change_set = change_set
        self.list_scopes: list[tuple[str, str]] = []

    def list_change_sets(self, project_id: str, variant_id: str) -> tuple[InboundTerminologyChangeSet, ...]:
        self.list_scopes.append((project_id, variant_id))
        if (project_id, variant_id) == (self.change_set.project_id, self.change_set.variant_id):
            return (self.change_set,)
        return ()

    def get_change_set(self, change_set_id: str) -> InboundTerminologyChangeSet:
        assert change_set_id == self.change_set.change_set_id
        return self.change_set


class _Importer:
    def __init__(self, preview: DraftImportProposal, result: DraftImportCommitResult) -> None:
        self.preview_result = preview
        self.commit_result = result
        self.previewed: list[DraftImportSelection] = []
        self.committed: list[tuple[DraftImportProposal, RequestContext]] = []

    def preview(self, selection: DraftImportSelection) -> DraftImportProposal:
        self.previewed.append(selection)
        return self.preview_result

    def commit(self, proposal: DraftImportProposal, context: RequestContext) -> DraftImportCommitResult:
        self.committed.append((proposal, context))
        return self.commit_result


def _fixture() -> tuple[
    DurableTerminologyInboundService,
    _Store,
    _Importer,
    DraftImportSelection,
    DraftImportProposal,
    DraftImportCommitResult,
]:
    change_set = cast(
        InboundTerminologyChangeSet,
        SimpleNamespace(
            change_set_id="change-set-1",
            project_id="project-1",
            variant_id="variant-1",
            content_digest="change-set-digest",
        ),
    )
    line = DraftLineState("project-1", "variant-1", 3, "version-1", "effective-digest")
    selection = DraftImportSelection(
        "change-set-1",
        "change-set-digest",
        0,
        line,
        (DraftImportChoice("item-1", InboundReviewDecision.ACCEPT),),
    )
    proposal = cast(DraftImportProposal, SimpleNamespace(selection=selection))
    result = cast(DraftImportCommitResult, object())
    store = _Store(change_set)
    importer = _Importer(proposal, result)
    return DurableTerminologyInboundService(store, importer), store, importer, selection, proposal, result


def _context(*, variant_id: str = "variant-1") -> RequestContext:
    return RequestContext("owner-1", project_id="project-1", variant_id=variant_id)


def test_durable_facade_scopes_queries_to_request_project_and_variant() -> None:
    service, store, _, _, _, _ = _fixture()

    assert service.list_inbound(_context())[0].change_set_id == "change-set-1"
    assert service.get_inbound(_context(), "change-set-1").content_digest == "change-set-digest"
    assert store.list_scopes == [("project-1", "variant-1")]
    with pytest.raises(PermissionError):
        service.get_inbound(_context(variant_id="other-variant"), "change-set-1")
    with pytest.raises(ValueError, match="Project and Variant"):
        service.list_inbound(RequestContext("owner-1", project_id="project-1"))


def test_durable_facade_delegates_explicit_preview_and_commit_without_placeholder_review() -> None:
    service, _, importer, selection, proposal, result = _fixture()
    context = _context()

    assert service.preview_import(selection) is proposal
    assert service.commit_import(proposal, context) is result
    assert importer.previewed == [selection]
    assert importer.committed == [(proposal, context)]


def test_durable_facade_rejects_stale_digest_before_preview() -> None:
    service, _, importer, selection, _, _ = _fixture()
    stale = DraftImportSelection(
        selection.change_set_id,
        "stale-digest",
        selection.expected_review_revision,
        selection.expected_line,
        selection.choices,
    )

    with pytest.raises(ValueError, match="durable change set"):
        service.preview_import(stale)
    assert importer.previewed == []
