from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.terminology.browse_queries import (
    TermBrowseQuery,
    TermBrowseStatus,
    TermFilter,
    TerminologyBrowseQueries,
    TerminologyBrowseUnavailableError,
)
from transbridge.application.terminology.drafts import new_draft
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.models import DecisionStatus, ScopeKind, TermDecision, TermScope
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology_profiles.browse_queries import ProfiledTerminologyBrowseSnapshotPort


def _term(index, **values):
    return replace(
        TermDecision(
            f"term-{index:04}",
            "project",
            "main",
            f"Term {index:04}",
            f"term {index:04}",
            f"译名 {index:04}",
            status=DecisionStatus.ADOPTED,
        ),
        **values,
    )


def _browse(decisions=()):
    state = SimpleNamespace(
        draft=new_draft(
            draft_id="draft",
            project_id="project",
            variant_id="main",
            base_version_id=None,
            base_content_digest="none",
            decisions=decisions,
        ),
        snapshot=EffectiveTerminologySnapshot(
            "project",
            "main",
            EffectiveSnapshotStatus.READY,
            version_id="version",
            content_digest="a" * 64,
            decisions=decisions,
        ),
    )
    drafts = SimpleNamespace(active_draft=lambda *args: state.draft)
    snapshots = SimpleNamespace(snapshot=lambda *args: state.snapshot)
    profiles = SimpleNamespace(selected_revision=lambda *args: None)
    browsing = TerminologyBrowseQueries(drafts, ProfiledTerminologyBrowseSnapshotPort(snapshots, profiles))
    return browsing, state


@pytest.mark.parametrize("kind", ["effective", "browsable_draft"])
def test_filter_and_pagination_cover_the_entire_collection(kind):
    browsing, _ = _browse(tuple(_term(index) for index in range(601)))
    read = getattr(browsing, f"list_{kind}_terms")
    query = TermBrowseQuery("project", "main")
    first = page = read(query, PageRequest(limit=73))
    items = list(page.items)
    while page.next_cursor is not None:
        page = read(query, PageRequest(limit=73, cursor=page.next_cursor))
        assert page.total == 601
        items.extend(page.items)
    assert len(items) == len({item.term_id for item in items}) == 601
    assert items[-1].original == "Term 0600"
    assert read(query, PageRequest(limit=73)) == first
    for search in ("ＴＥＲＭ  0600", "译名 0600"):
        result = read(replace(query, filters=TermFilter(search=search)))
        assert result.items == (items[-1],)
        assert result.total == 1


def test_scope_and_status_filters_do_not_confuse_review_or_excluded_with_effective():
    decisions = (
        _term(0),
        _term(1, scope=TermScope.plugin("A.esm"), status=DecisionStatus.MANUAL_CONFIRMED),
        _term(2, status=DecisionStatus.REVIEW_REQUIRED),
        _term(3, status=DecisionStatus.UNRESOLVED),
        _term(4, status=DecisionStatus.REVIEW_REQUIRED, suppressed=True),
    )
    browsing, _ = _browse(decisions)
    query = TermBrowseQuery("project", "main")
    assert browsing.list_effective_terms(query).items == decisions[:2]
    expected = {
        TermBrowseStatus.EFFECTIVE: decisions[:2],
        TermBrowseStatus.REVIEW_REQUIRED: decisions[2:3],
        TermBrowseStatus.UNRESOLVED: decisions[3:4],
        TermBrowseStatus.EXCLUDED: decisions[4:],
    }
    for status, items in expected.items():
        assert browsing.list_browsable_draft_terms(replace(query, filters=TermFilter(status=status))).items == items
    filtered = replace(query, filters=TermFilter(scope=ScopeKind.PLUGIN, status=TermBrowseStatus.EFFECTIVE))
    assert browsing.list_browsable_draft_terms(filtered).items == decisions[1:2]


@pytest.mark.parametrize(
    "change", ["draft_id", "revision", "base_version_id", "base_content_digest", "decision_set_digest"]
)
def test_draft_cursor_binds_the_full_draft_identity_even_when_rows_are_unchanged(change):
    browsing, state = _browse((_term(0), _term(1)))
    query = TermBrowseQuery("project", "main")
    page = browsing.list_browsable_draft_terms(query, PageRequest(limit=1))
    new_value = 1 if change == "revision" else "changed"
    state.draft = replace(state.draft, ref=replace(state.draft.ref, **{change: new_value}))
    with pytest.raises(CursorStaleError):
        browsing.list_browsable_draft_terms(query, PageRequest(cursor=page.next_cursor))


@pytest.mark.parametrize("change", ["filter", "kind", "snapshot", "invalid_position"])
def test_cursor_rejects_changed_query_snapshot_and_invalid_position(change):
    browsing, state = _browse((_term(0), _term(1)))
    query = TermBrowseQuery("project", "main")
    page = browsing.list_effective_terms(query, PageRequest(limit=1))
    cursor = page.next_cursor
    read = browsing.list_effective_terms
    if change == "filter":
        query = replace(query, filters=TermFilter(search="term"))
    elif change == "kind":
        read = browsing.list_browsable_draft_terms
    elif change == "snapshot":
        state.snapshot = replace(state.snapshot, version_id="next-version")
    else:
        cursor = replace(cursor, sort_values=("invalid",), stable_id="invalid")
    with pytest.raises(CursorStaleError):
        read(query, PageRequest(cursor=cursor))


def test_empty_draft_and_unpublished_base_have_stable_empty_pages():
    browsing, state = _browse()
    state.draft = None
    state.snapshot = EffectiveTerminologySnapshot("project", "main", EffectiveSnapshotStatus.NO_PROJECT_VERSION)
    query = TermBrowseQuery("project", "main")
    for read in (browsing.list_effective_terms, browsing.list_browsable_draft_terms):
        page = read(query)
        assert page.items == () and page.total == 0 and page.next_cursor is None
        assert page == read(query)


@pytest.mark.parametrize("status", [EffectiveSnapshotStatus.UNAVAILABLE, EffectiveSnapshotStatus.CORRUPT])
def test_unreadable_effective_assets_report_the_reason_instead_of_an_empty_list(status):
    browsing, state = _browse()
    state.snapshot = EffectiveTerminologySnapshot("project", "main", status, diagnostics=("missing exact version",))
    query = TermBrowseQuery("project", "main")
    assert browsing.describe(query).diagnostics == ("missing exact version",)
    with pytest.raises(TerminologyBrowseUnavailableError, match="missing exact version"):
        browsing.list_effective_terms(query)


def test_snapshot_and_draft_from_another_line_are_rejected():
    browsing, _ = _browse((_term(0),))
    query = TermBrowseQuery("other-project", "other-variant")
    with pytest.raises(ValueError, match="another Project"):
        browsing.list_effective_terms(query)
    with pytest.raises(ValueError, match="another Project"):
        browsing.list_browsable_draft_terms(query)
