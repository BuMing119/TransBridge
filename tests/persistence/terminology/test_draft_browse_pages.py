from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.persistence.terminology.test_browse_pages import fixture  # noqa: F401
from transbridge.application.terminology.browse_queries import (
    TermBrowseQuery,
    TermBrowseStatus,
    TermFilter,
    TerminologyBrowseQueries,
)
from transbridge.application.terminology.drafts import revised_draft
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.models import DecisionStatus, ScopeKind, TermScope
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.persistence.terminology.draft_browse_pages import DraftBrowsePages


def test_draft_metadata_page_matches_full_reader_and_decodes_only_visible_rows(fixture, monkeypatch):  # noqa: F811
    repository, _, _, _ = fixture
    draft = repository.active_draft("project-1", "variant-1")
    changed = revised_draft(
        draft,
        decisions=(
            replace(draft.decisions[0], suppressed=True),
            replace(draft.decisions[1], status=DecisionStatus.REVIEW_REQUIRED),
            replace(draft.decisions[2], status=DecisionStatus.UNRESOLVED),
            replace(draft.decisions[3], scope=TermScope.plugin("Example.esp")),
            *draft.decisions[4:],
        ),
    )
    repository.update_draft(changed, expected_revision=draft.ref.revision)
    reference = TerminologyBrowseQueries(SimpleNamespace(active_draft=lambda *_: changed), None)
    reader = DraftBrowsePages(repository)

    def forbid(*_):
        pytest.fail("paged reads must not decode the complete draft")

    monkeypatch.setattr(repository._drafts, "_decode", forbid)
    for filters in (
        TermFilter(),
        TermFilter(search="000034"),
        TermFilter(scope=ScopeKind.PLUGIN),
        *(TermFilter(status=status) for status in TermBrowseStatus),
    ):
        query = TermBrowseQuery("project-1", "variant-1", filters)
        actual = reader.page(query, PageRequest(limit=5))
        expected = reference.list_browsable_draft_terms(query, PageRequest(limit=5))
        assert (actual.items, actual.total, actual.snapshot_digest) == (
            expected.items,
            expected.total,
            expected.snapshot_digest,
        )
        if actual.next_cursor is not None:
            second = reader.page(query, PageRequest(limit=5, cursor=actual.next_cursor))
            reference_second = reference.list_browsable_draft_terms(
                query, PageRequest(limit=5, cursor=expected.next_cursor)
            )
            assert second.items == reference_second.items


def test_revised_draft_rejects_cursor_and_cancelled_page_returns_nothing(fixture):  # noqa: F811
    repository, _, _, _ = fixture
    reader = DraftBrowsePages(repository)
    query = TermBrowseQuery("project-1", "variant-1")
    first = reader.page(query, PageRequest(limit=1))
    draft = repository.active_draft("project-1", "variant-1")
    changed = revised_draft(draft, decisions=(replace(draft.decisions[0], translation="Updated"), *draft.decisions[1:]))
    repository.update_draft(changed, expected_revision=draft.ref.revision)
    with pytest.raises(CursorStaleError):
        reader.page(query, PageRequest(cursor=first.next_cursor))
    assert reader.page(query, PageRequest()).items[0].translation == "Updated"
    cancellation = QueryCancellation()
    cancellation.cancel()
    with pytest.raises(QueryCancelled):
        reader.page(query, PageRequest(), cancellation=cancellation)
