from dataclasses import replace
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.benchmark_terminology_storage import PROJECT, VARIANT, create_fixture, open_browse
from transbridge.application.terminology.browse_queries import TermBrowseQuery, TermFilter, TerminologyBrowseQueries
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology.query_control import QueryCancellation, QueryCancelled
from transbridge.application.terminology_profiles.browse_queries import ProfiledTerminologyBrowseSnapshotPort
from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent


@pytest.fixture
def fixture(tmp_path):
    path = create_fixture(tmp_path / "database", 35)
    repository, profiles, base, browsing = open_browse(path)
    try:
        yield repository, profiles, base, browsing
    finally:
        repository.close()


def test_direct_pages_match_verified_projection_without_loading_whole_version(fixture):
    repository, profiles, base, browsing = fixture
    reference = TerminologyBrowseQueries(
        repository,
        ProfiledTerminologyBrowseSnapshotPort(SimpleNamespace(snapshot=base.snapshot), profiles),
    )
    query = TermBrowseQuery(PROJECT, VARIANT)
    statements = []
    repository._connection.set_trace_callback(statements.append)
    first = browsing.list_effective_terms(query, PageRequest(limit=7))
    repository._connection.set_trace_callback(None)
    assert not any("payload_json FROM versions" in s for s in statements)
    assert len([s for s in statements if s.startswith("SELECT stable_id, payload_json FROM version_terms")]) == 1
    expected = reference.list_effective_terms(query, PageRequest(limit=7))
    assert (first.items, first.total, first.snapshot_digest) == (
        expected.items,
        expected.total,
        expected.snapshot_digest,
    )
    second = browsing.list_effective_terms(query, PageRequest(limit=7, cursor=first.next_cursor))
    assert [i.term_id for i in second.items] == [f"term-{i:06}" for i in range(7, 14)]
    search = replace(query, filters=TermFilter(search="方案 术语 000034"))
    assert browsing.list_effective_terms(search).items == reference.list_effective_terms(search).items


def test_profile_switch_invalidates_cursor_and_can_show_independent_rows(fixture):
    _, profiles, _, browsing = fixture
    query = TermBrowseQuery(PROJECT, VARIANT)
    first = browsing.list_effective_terms(query, PageRequest(limit=1))
    profile = profiles.create_with_content(
        PROJECT, "Independent", TerminologyProfileContent((ProfileTermMapping("New term", "新词"),))
    )
    profiles.publish(profile.profile_id, expected_draft_revision=0)
    profiles.select(PROJECT, VARIANT, profile.profile_id)
    with pytest.raises(CursorStaleError):
        browsing.list_effective_terms(query, PageRequest(cursor=first.next_cursor))
    current = browsing.list_effective_terms(query)
    assert [(i.original, i.translation) for i in current.items] == [("New term", "新词")]


def test_external_row_change_invalidates_cached_metadata_and_cursor(fixture):
    repository, profiles, _, browsing = fixture
    # The underlying immutable row cannot normally change; simulate an external
    # repair without changing the version identity to exercise data_version.
    profiles.clear_selection(PROJECT, VARIANT)
    query = TermBrowseQuery(PROJECT, VARIANT)
    first = browsing.list_effective_terms(query, PageRequest(limit=1))
    with sqlite3.connect(repository.path, isolation_level=None) as connection:
        connection.execute("DROP TRIGGER immutable_version_terms_update")
        connection.execute(
            "UPDATE version_terms SET payload_json=json_set(payload_json,'$.fields.translation','Changed') "
            "WHERE stable_id='term-000000'"
        )
    with pytest.raises(CursorStaleError):
        browsing.list_effective_terms(query, PageRequest(cursor=first.next_cursor))
    assert browsing.list_effective_terms(query).items[0].translation == "Changed"


def test_cancelled_direct_page_never_returns_warm_rows(fixture):
    _, _, _, browsing = fixture
    query = TermBrowseQuery(PROJECT, VARIANT)
    browsing.list_effective_terms(query)
    cancellation = QueryCancellation()
    cancellation.cancel()
    with pytest.raises(QueryCancelled):
        browsing.list_effective_terms(query, cancellation=cancellation)
