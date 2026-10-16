from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.application.terminology.test_browse_queries import _browse, _term
from transbridge.application.terminology import browse_index
from transbridge.application.terminology.browse_queries import TermBrowseQuery, TermFilter
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.application.terminology_profiles import (
    browse_queries as profile_browsing,
    effective as profile_effective,
)
from transbridge.application.terminology_profiles.browse_queries import ProfiledTerminologyBrowseSnapshotPort
from transbridge.application.terminology_profiles.in_memory import InMemoryTerminologyProfileRepository
from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent
from transbridge.application.terminology_profiles.service import TerminologyProfileService


class _CancelAfter(QueryCancellation):
    def __init__(self, checks: int):
        super().__init__()
        self.remaining = checks

    def raise_if_cancelled(self) -> None:
        self.remaining -= 1
        if self.remaining <= 0:
            self.cancel()
        super().raise_if_cancelled()


@pytest.mark.parametrize("kind", ["effective", "browsable_draft"])
def test_warm_pages_reuse_normalized_order_and_full_filter_result(monkeypatch, kind) -> None:
    decisions = tuple(_term(index) for index in range(30))
    browsing, _ = _browse(decisions)
    read = getattr(browsing, f"list_{kind}_terms")
    query = TermBrowseQuery("project", "main", TermFilter(search="term"))
    first = read(query, PageRequest(limit=7))
    index = next(iter(browsing._indexes.values()))
    rows = index._rows

    def forbid_projection(*args):
        pytest.fail("warm paging must reuse normalized text and the completed filter")

    monkeypatch.setattr(browse_index, "normalize_original", forbid_projection)
    monkeypatch.setattr(TermFilter, "matches_normalized", forbid_projection)
    second = read(query, PageRequest(limit=7, cursor=first.next_cursor))
    assert first.items == decisions[:7]
    assert second.items == decisions[7:14]
    assert second.total == 30
    assert next(iter(browsing._indexes.values()))._rows is rows


def test_snapshot_and_filter_caches_have_bounded_lru_capacity() -> None:
    browsing, state = _browse(tuple(_term(index) for index in range(20)))
    query = TermBrowseQuery("project", "main")
    browsing.list_browsable_draft_terms(query)
    first_key, first_index = next(iter(browsing._indexes.items()))
    for value in range(10):
        filtered = replace(query, filters=TermFilter(search=f"{value:04}"))
        result = browsing.list_browsable_draft_terms(filtered)
        assert result.items == (_term(value),)
    assert len(first_index._matches) == 8
    assert TermFilter(search="0000") not in first_index._matches
    retained = TermFilter(search="0002")
    browsing.list_browsable_draft_terms(replace(query, filters=retained))
    browsing.list_browsable_draft_terms(replace(query, filters=TermFilter(search="0010")))
    assert retained in first_index._matches
    assert TermFilter(search="0003") not in first_index._matches

    for revision in (1, 2):
        state.draft = replace(state.draft, ref=replace(state.draft.ref, revision=revision))
        assert browsing.list_browsable_draft_terms(query).total == 20
    assert len(browsing._indexes) == 2
    assert first_key not in browsing._indexes


def test_cancelled_projection_does_not_install_partial_index() -> None:
    browsing, _ = _browse(tuple(_term(index) for index in range(30)))
    query = TermBrowseQuery("project", "main")
    with pytest.raises(QueryCancelled):
        browsing.list_browsable_draft_terms(query, cancellation=_CancelAfter(8))
    assert not browsing._indexes
    assert browsing.list_browsable_draft_terms(query).total == 30


def test_cancelled_filter_does_not_install_partial_match_result() -> None:
    browsing, _ = _browse(tuple(_term(index) for index in range(30)))
    query = TermBrowseQuery("project", "main")
    browsing.list_browsable_draft_terms(query)
    index = next(iter(browsing._indexes.values()))
    filtered = replace(query, filters=TermFilter(search="term 000"))
    with pytest.raises(QueryCancelled):
        browsing.list_browsable_draft_terms(filtered, cancellation=_CancelAfter(8))
    assert filtered.filters not in index._matches
    result = browsing.list_browsable_draft_terms(filtered)
    assert result.total == 10
    assert result.items == tuple(_term(index) for index in range(10))


def test_cancelled_warm_request_never_delivers_a_cached_page() -> None:
    browsing, _ = _browse((_term(0), _term(1)))
    query = TermBrowseQuery("project", "main")
    first = browsing.list_browsable_draft_terms(query, PageRequest(limit=1))
    cancellation = QueryCancellation()
    cancellation.cancel()
    with pytest.raises(QueryCancelled):
        browsing.list_browsable_draft_terms(
            query, PageRequest(limit=1, cursor=first.next_cursor), cancellation=cancellation
        )


@pytest.mark.parametrize("stage", ["mapping_index", "overlay", "mapping_rows"])
def test_cancelled_profile_overlay_does_not_install_partial_context(monkeypatch, stage) -> None:
    browsing, state = _browse(tuple(_term(index) for index in range(30)))
    profiles = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    profile = profiles.create_with_content(
        "project",
        "scheme",
        TerminologyProfileContent(
            mappings=tuple(ProfileTermMapping(item.original, "方案译名") for item in state.snapshot.decisions)
        ),
    )
    profiles.publish(profile.profile_id, expected_draft_revision=0)
    profiles.select("project", "main", profile.profile_id)
    browsing._snapshots = ProfiledTerminologyBrowseSnapshotPort(
        SimpleNamespace(snapshot=lambda *args: state.snapshot), profiles
    )
    query = TermBrowseQuery("project", "main")
    cancellation = QueryCancellation()
    target = profile_effective if stage == "overlay" else profile_browsing
    descriptor = ProfileTermMapping.term_key
    original_key = target.logical_term_key

    def read_mapping_key(item):
        return descriptor.__get__(item, ProfileTermMapping)

    if stage == "mapping_index":
        original_key = read_mapping_key
    calls = 0

    def cancel_during_overlay(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            cancellation.cancel()
        return original_key(*args, **kwargs)

    with monkeypatch.context() as patch:
        if stage == "mapping_index":
            patch.setattr(ProfileTermMapping, "term_key", property(cancel_during_overlay))
        else:
            patch.setattr(target, "logical_term_key", cancel_during_overlay)
        with pytest.raises(QueryCancelled):
            browsing.list_effective_terms(query, cancellation=cancellation)
    assert calls == 3
    assert not browsing._snapshots._cache
    assert not browsing._indexes
    result = browsing.list_effective_terms(query)
    assert result.total == 30
    assert all(item.translation == "方案译名" for item in result.items)
