from dataclasses import replace
import json
import sqlite3

import pytest

from tests.persistence.terminology.test_effective_snapshot import _publish
from transbridge.application.terminology.browse_queries import TermBrowseQuery, TerminologyBrowseQueries
from transbridge.application.terminology.drafts import new_draft, revised_draft
from transbridge.application.terminology.effective import EffectiveSnapshotStatus
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology_profiles.browse_queries import ProfiledTerminologyBrowseSnapshotPort
from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent
from transbridge.application.terminology_profiles.service import TerminologyProfileService
from transbridge.persistence.terminology import SqliteEffectiveTerminologySnapshotPort, SqliteTerminologyRepository
from transbridge.persistence.terminology.codec import dumps
from transbridge.persistence.terminology.connection import TerminologyStorageError
from transbridge.persistence.terminology.profiles import SqliteTerminologyProfileRepository

PROJECT, VARIANT = "project-1", "variant-1"


@pytest.fixture
def storage(tmp_path):
    repository = SqliteTerminologyRepository.open(str(tmp_path), PROJECT)
    try:
        yield repository
    finally:
        repository.close()


def _browsing(repository):
    profiles = TerminologyProfileService(repository.localization_profiles)
    base = SqliteEffectiveTerminologySnapshotPort(repository)
    snapshots = ProfiledTerminologyBrowseSnapshotPort(base, profiles)
    return TerminologyBrowseQueries(repository, snapshots), profiles, base


def _draft(repository):
    decisions = tuple(
        TermDecision(
            f"term-{index}",
            PROJECT,
            VARIANT,
            f"Term {index}",
            f"term {index}",
            f"译名 {index}",
            status=DecisionStatus.ADOPTED,
        )
        for index in range(4)
    )
    draft = new_draft(
        draft_id="draft",
        project_id=PROJECT,
        variant_id=VARIANT,
        base_version_id=None,
        base_content_digest="none",
        decisions=decisions,
    )
    repository.create_draft(draft)
    return draft


def _profile(profiles, name="方案 A", translation="巨龙"):
    profile = profiles.create_with_content(
        PROJECT, name, TerminologyProfileContent(mappings=(ProfileTermMapping("Dragon", translation),))
    )
    profiles.publish(profile.profile_id, expected_draft_revision=0)
    profiles.select(PROJECT, VARIANT, profile.profile_id)
    return profile


def test_warm_effective_profile_and_draft_reads_do_not_decode_again(storage, monkeypatch) -> None:
    _publish(storage)
    browsing, profiles, _ = _browsing(storage)
    _profile(profiles)
    query = TermBrowseQuery(PROJECT, VARIANT)
    effective = browsing.list_effective_terms(query)
    before = storage.active_draft(PROJECT, VARIANT)
    if before is None:
        version = storage.effective_version(PROJECT, VARIANT)
        before = new_draft(
            draft_id="new-draft",
            project_id=PROJECT,
            variant_id=VARIANT,
            base_version_id=version.ref.version_id,
            base_content_digest=version.ref.content_digest,
            decisions=version.decisions,
        )
        storage.create_draft(before)
    draft = browsing.list_browsable_draft_terms(query)
    # The draft write invalidates immutable read caches once before the warm check.
    browsing.list_effective_terms(query)

    def forbid_decode(*args):
        pytest.fail("warm reads must not deserialize the unchanged full assets")

    monkeypatch.setattr(storage, "_decode", forbid_decode)
    monkeypatch.setattr(storage._drafts, "_decode", forbid_decode)
    monkeypatch.setattr(storage.localization_profiles._reads, "_decode", forbid_decode)
    assert browsing.list_effective_terms(query) == effective
    assert browsing.list_browsable_draft_terms(query) == draft


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("invalid_json", [False, True])
def test_effective_cache_cannot_hide_corrupt_payload_after_commit(storage, external, invalid_json) -> None:
    _publish(storage)
    _, _, reader = _browsing(storage)
    assert reader.snapshot(PROJECT, VARIANT).status is EffectiveSnapshotStatus.READY
    connection = sqlite3.connect(storage.path, isolation_level=None) if external else storage._connection
    try:
        connection.execute("DROP TRIGGER immutable_versions_update")
        payload = "{invalid"
        if not invalid_json:
            value = json.loads(connection.execute("SELECT payload_json FROM versions").fetchone()[0])
            value["fields"]["decisions"]["$tuple"][0]["fields"]["translation"] = "tampered"
            payload = json.dumps(value)
        connection.execute("UPDATE versions SET payload_json = ?", (payload,))
    finally:
        if external:
            connection.close()
    snapshot = reader.snapshot(PROJECT, VARIANT)
    assert snapshot.status is EffectiveSnapshotStatus.CORRUPT
    assert snapshot.decisions == ()


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("invalid_json", [False, True])
def test_profile_cache_cannot_hide_corrupt_payload_after_commit(storage, external, invalid_json) -> None:
    _, profiles, _ = _browsing(storage)
    _profile(profiles)
    assert profiles.selected_revision(PROJECT, VARIANT).content.mappings[0].translation == "巨龙"
    connection = sqlite3.connect(storage.path, isolation_level=None) if external else storage._connection
    try:
        connection.execute("DROP TRIGGER immutable_terminology_profile_revisions_update")
        payload = "{invalid"
        if not invalid_json:
            value = json.loads(
                connection.execute("SELECT content_json FROM terminology_profile_revisions").fetchone()[0]
            )
            value["value"]["mappings"][0]["translation"] = "tampered"
            payload = json.dumps(value)
        connection.execute("UPDATE terminology_profile_revisions SET content_json = ?", (payload,))
    finally:
        if external:
            connection.close()
    with pytest.raises(ValueError):
        profiles.selected_revision(PROJECT, VARIANT)


@pytest.mark.parametrize("external", [False, True])
def test_draft_cache_cannot_hide_invalid_json_after_commit(storage, external) -> None:
    _draft(storage)
    browsing, _, _ = _browsing(storage)
    query = TermBrowseQuery(PROJECT, VARIANT)
    assert browsing.list_browsable_draft_terms(query).total == 4
    connection = sqlite3.connect(storage.path, isolation_level=None) if external else storage._connection
    try:
        connection.execute("UPDATE drafts SET payload_json = '{invalid'")
    finally:
        if external:
            connection.close()
    with pytest.raises(TerminologyStorageError):
        browsing.list_browsable_draft_terms(query)


@pytest.mark.parametrize(
    "field", ["draft_id", "revision", "base_version_id", "base_content_digest", "decision_set_digest"]
)
def test_cached_draft_cursor_rejects_every_identity_change(storage, field) -> None:
    original = _draft(storage)
    browsing, _, _ = _browsing(storage)
    query = TermBrowseQuery(PROJECT, VARIANT)
    page = browsing.list_browsable_draft_terms(query, PageRequest(limit=1))
    changed = replace(original, ref=replace(original.ref, **{field: 1 if field == "revision" else "changed"}))
    with sqlite3.connect(storage.path, isolation_level=None) as connection:
        connection.execute(
            f"UPDATE drafts SET {field} = ?, payload_json = ?", (getattr(changed.ref, field), dumps(changed))
        )
    with pytest.raises(CursorStaleError):
        browsing.list_browsable_draft_terms(query, PageRequest(cursor=page.next_cursor))
    assert browsing.list_browsable_draft_terms(query).items == original.decisions


def test_profile_summaries_do_not_read_or_decode_draft_json(storage) -> None:
    _, profiles, _ = _browsing(storage)
    profile = _profile(profiles)
    storage._connection.execute("UPDATE terminology_profiles SET draft_json = '{invalid'")
    statements = []
    storage._connection.set_trace_callback(statements.append)
    summaries = profiles.list_profile_summaries(PROJECT)
    assert [(item.profile_id, item.name) for item in summaries] == [(profile.profile_id, profile.name)]
    assert not any("draft_json" in statement.lower() for statement in statements)


@pytest.mark.parametrize("external", [False, True])
def test_publish_switch_and_archive_refresh_profile_browse(storage, external) -> None:
    _publish(storage)
    browsing, profiles, _ = _browsing(storage)
    profile = _profile(profiles)
    query = TermBrowseQuery(PROJECT, VARIANT)
    assert browsing.list_effective_terms(query).items[0].translation == "巨龙"
    connection = sqlite3.connect(storage.path, isolation_level=None) if external else None
    writer = TerminologyProfileService(SqliteTerminologyProfileRepository(connection)) if external else profiles
    try:
        saved = writer.save_draft(
            profile.profile_id,
            TerminologyProfileContent(mappings=(ProfileTermMapping("Dragon", "古龙"),)),
            expected_revision=0,
        )
        writer.publish(profile.profile_id, expected_draft_revision=saved.draft_revision)
        assert browsing.list_effective_terms(query).items[0].translation == "古龙"
        other = _profile(writer, "方案 B", "飞龙")
        assert browsing.list_effective_terms(query).items[0].translation == "飞龙"
        writer.archive(other.profile_id, expected_selections=writer.list_profile_selections(other.profile_id))
        assert browsing.list_effective_terms(query).items[0].translation == "龙"
    finally:
        if connection is not None:
            connection.close()


def test_closed_repository_cannot_return_warm_effective_or_profile_data(tmp_path) -> None:
    repository = SqliteTerminologyRepository.open(str(tmp_path), PROJECT)
    _publish(repository)
    browsing, profiles, reader = _browsing(repository)
    _profile(profiles)
    browsing.list_effective_terms(TermBrowseQuery(PROJECT, VARIANT))
    repository.close()
    assert reader.snapshot(PROJECT, VARIANT).status is not EffectiveSnapshotStatus.READY
    with pytest.raises(sqlite3.Error):
        profiles.selected_revision(PROJECT, VARIANT)
    with pytest.raises(sqlite3.Error):
        browsing.list_browsable_draft_terms(TermBrowseQuery(PROJECT, VARIANT))


def test_draft_reads_after_rollback_do_not_return_uncommitted_cache(storage) -> None:
    original = _draft(storage)
    browsing, _, _ = _browsing(storage)
    query = TermBrowseQuery(PROJECT, VARIANT)
    browsing.list_browsable_draft_terms(query)
    changed = revised_draft(original, decisions=(replace(original.decisions[0], translation="未提交"),))
    storage._connection.execute("BEGIN IMMEDIATE")
    try:
        storage._drafts.update_current(original, changed)
        assert browsing.list_browsable_draft_terms(query).items[0].translation == "未提交"
    finally:
        storage._connection.rollback()
    assert browsing.list_browsable_draft_terms(query).items == original.decisions


def test_effective_profile_and_composition_caches_keep_only_two_snapshots(storage) -> None:
    browsing, profiles, reader = _browsing(storage)
    profile_ids = tuple(_profile(profiles, f"方案 {index}").profile_id for index in range(3))
    for profile_id in profile_ids:
        assert profiles.published_revision(profile_id, 1).profile_id == profile_id
    published_cache = storage.localization_profiles._reads._published
    assert len(published_cache) == 2
    assert (profile_ids[0], 1) not in published_cache
    assert profiles.published_revision(profile_ids[1], 1).profile_id == profile_ids[1]
    assert profiles.published_revision(profile_ids[0], 1).profile_id == profile_ids[0]
    assert (profile_ids[2], 1) not in published_cache

    for index in range(3):
        context = browsing.describe(TermBrowseQuery(PROJECT, f"variant-{index}"))
        assert context.effective_snapshot.local_variant_id == f"variant-{index}"
    assert len(reader._cache) == 2
    assert len(browsing._snapshots._cache) == 2
    with pytest.raises(ValueError, match="another Project"):
        browsing.describe(TermBrowseQuery("other-project", VARIANT))


@pytest.mark.parametrize("external", [False, True])
def test_changed_payload_with_same_ref_does_not_reuse_old_draft_index(storage, external) -> None:
    original = _draft(storage)
    browsing, _, _ = _browsing(storage)
    query = TermBrowseQuery(PROJECT, VARIANT)
    browsing.list_browsable_draft_terms(query)
    changed = replace(original, decisions=(replace(original.decisions[0], translation="changed"),))
    connection = sqlite3.connect(storage.path, isolation_level=None) if external else storage._connection
    try:
        connection.execute("UPDATE drafts SET payload_json = ?", (dumps(changed),))
    finally:
        if external:
            connection.close()
    # A payload read after a storage change may fail closed, but cannot display
    # the previous cached collection as though the new payload were examined.
    try:
        page = browsing.list_browsable_draft_terms(query)
    except (TerminologyStorageError, RuntimeError, ValueError):
        return
    assert page.items == changed.decisions
