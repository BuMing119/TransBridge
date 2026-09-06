from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from transbridge.application.terminology_profiles.models import (
    ProfileEntryOverride,
    ProfileOccurrenceBinding,
    ProfileState,
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)
from transbridge.persistence.terminology.profiles import SqliteTerminologyProfileRepository
from transbridge.persistence.terminology.schema import SCHEMA_VERSION, initialize_schema, validate_schema


@pytest.fixture
def storage() -> tuple[sqlite3.Connection, SqliteTerminologyProfileRepository]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    initialize_schema(connection)
    repository = SqliteTerminologyProfileRepository(connection)
    try:
        yield connection, repository
    finally:
        connection.close()


def _content(suffix: str = "官译") -> TerminologyProfileContent:
    mapping = ProfileTermMapping(
        original="Whiterun",
        base_translation="白漫城",
        translation=f"雪漫城-{suffix}",
    )
    return TerminologyProfileContent(
        mappings=(
            mapping,
            ProfileTermMapping(
                original="Jarl",
                base_translation="领主",
                translation=f"领主-{suffix}",
                scope_kind="plugin",
                plugin_id="Example.esp",
            ),
        ),
        overrides=(ProfileEntryOverride("entry-override", f"整句覆盖-{suffix}"),),
        bindings=(
            ProfileOccurrenceBinding(
                entry_key="entry-bound",
                term_key=mapping.term_key,
                start=0,
                end=3,
                expected_text="白漫城",
            ),
        ),
    )


def _profile(profile_id: str = "profile-1", project_id: str = "project-1") -> TerminologyProfile:
    return TerminologyProfile(
        profile_id=profile_id,
        project_id=project_id,
        name=f"Profile {profile_id}",
        created_at="2026-09-06T00:00:00+00:00",
        updated_at="2026-09-06T00:00:00+00:00",
    )


def _published(profile: TerminologyProfile, revision: int = 1) -> PublishedTerminologyProfile:
    return PublishedTerminologyProfile(
        profile_id=profile.profile_id,
        project_id=profile.project_id,
        revision=revision,
        name=profile.name,
        content_digest=profile.draft.content_digest,
        content=profile.draft,
        published_at=f"2026-09-06T00:00:0{revision}+00:00",
    )


def test_schema_v4_and_profile_draft_round_trip_with_cas(storage) -> None:
    connection, repository = storage
    profile = _profile()
    repository.insert_profile(profile)

    saved = repository.save_draft(
        profile.profile_id,
        _content(),
        expected_revision=0,
        updated_at="2026-09-06T00:00:01+00:00",
    )
    renamed = repository.rename_profile(
        profile.profile_id,
        "Skyrim 官译",
        updated_at="2026-09-06T00:00:02+00:00",
    )

    assert SCHEMA_VERSION == 4
    assert validate_schema(connection) is None
    assert saved.draft_revision == 1
    assert renamed.draft == _content()
    assert repository.get_profile(profile.profile_id) == renamed
    assert repository.list_profiles(profile.project_id) == (renamed,)
    with pytest.raises(ValueError, match="stale draft revision"):
        repository.save_draft(
            profile.profile_id,
            _content("民间"),
            expected_revision=0,
            updated_at="2026-09-06T00:00:03+00:00",
        )
    assert repository.get_profile(profile.profile_id) == renamed


def test_published_revisions_are_sequential_and_immutable(storage) -> None:
    connection, repository = storage
    repository.insert_profile(_profile())
    draft_one = repository.save_draft(
        "profile-1",
        _content("一版"),
        expected_revision=0,
        updated_at="2026-09-06T00:00:01+00:00",
    )
    revision_one = _published(draft_one)
    repository.insert_published(revision_one, expected_draft_revision=1)

    assert repository.get_published("profile-1", 1) == revision_one
    assert repository.get_profile("profile-1").latest_published_revision == 1
    with pytest.raises(ValueError, match="already exists|not next"):
        repository.insert_published(revision_one, expected_draft_revision=1)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("UPDATE terminology_profile_revisions SET name = 'changed' WHERE profile_id = 'profile-1'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM terminology_profile_revisions WHERE profile_id = 'profile-1'")

    draft_two = repository.save_draft(
        "profile-1",
        _content("二版"),
        expected_revision=1,
        updated_at="2026-09-06T00:00:02+00:00",
    )
    revision_two = _published(draft_two, revision=2)
    repository.insert_published(revision_two, expected_draft_revision=2)
    assert repository.get_published("profile-1", 1) == revision_one
    assert repository.get_published("profile-1", 2) == revision_two


def test_publish_and_selection_recheck_current_profile_state_inside_storage_transaction(storage) -> None:
    _connection, repository = storage
    repository.insert_profile(_profile())
    stale_draft = repository.save_draft(
        "profile-1",
        _content("旧草稿"),
        expected_revision=0,
        updated_at="2026-09-06T00:00:01+00:00",
    )
    current_draft = repository.save_draft(
        "profile-1",
        _content("新草稿"),
        expected_revision=1,
        updated_at="2026-09-06T00:00:02+00:00",
    )

    with pytest.raises(ValueError, match="draft changed"):
        repository.insert_published(_published(stale_draft), expected_draft_revision=1)

    current = _published(current_draft)
    repository.insert_published(current, expected_draft_revision=2)
    repository.set_archived(
        "profile-1",
        archived=True,
        updated_at="2026-09-06T00:00:03+00:00",
    )
    with pytest.raises(ValueError, match="unavailable"):
        repository.set_selection(
            TerminologyProfileSelection(
                "project-1",
                "variant-a",
                "profile-1",
                1,
                "2026-09-06T00:00:04+00:00",
            )
        )


def test_selection_isolated_by_project_and_variant_and_archive_clears_it(storage) -> None:
    connection, repository = storage
    profile = _profile()
    repository.insert_profile(profile)
    repository.insert_published(_published(profile), expected_draft_revision=0)
    variant_a = TerminologyProfileSelection(
        "project-1",
        "variant-a",
        "profile-1",
        1,
        "2026-09-06T00:00:01+00:00",
    )
    variant_b = replace(variant_a, variant_id="variant-b")
    repository.set_selection(variant_a)
    repository.set_selection(variant_b)

    assert repository.get_selection("project-1", "variant-a") == variant_a
    assert repository.get_selection("project-1", "variant-b") == variant_b
    assert repository.get_selection("project-2", "variant-a") is None

    draft = repository.save_draft(
        "profile-1",
        _content("二版"),
        expected_revision=0,
        updated_at="2026-09-06T00:00:02+00:00",
    )
    revision_two = _published(draft, revision=2)
    repository.insert_published(revision_two, expected_draft_revision=1)
    selected_a = repository.get_selection("project-1", "variant-a")
    selected_b = repository.get_selection("project-1", "variant-b")
    assert selected_a is not None and selected_a.revision == 2
    assert selected_b is not None and selected_b.revision == 2

    with pytest.raises(ValueError, match="unavailable"):
        repository.set_selection(replace(variant_a, project_id="project-2", revision=2))

    repository.clear_selection("project-1", "variant-a")
    assert repository.get_selection("project-1", "variant-a") is None
    assert repository.get_selection("project-1", "variant-b") == selected_b
    repository.set_selection(replace(variant_a, revision=2))
    archived = repository.set_archived(
        "profile-1",
        archived=True,
        updated_at="2026-09-06T00:00:02+00:00",
    )
    assert archived.state is ProfileState.ARCHIVED
    assert repository.list_profiles("project-1") == ()
    assert repository.list_profiles("project-1", include_archived=True) == (archived,)
    assert repository.get_selection("project-1", "variant-a") is None
    assert repository.get_selection("project-1", "variant-b") is None

    restored = repository.set_archived(
        "profile-1",
        archived=False,
        updated_at="2026-09-06T00:00:03+00:00",
    )
    assert restored.state is ProfileState.ACTIVE
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        connection.execute("DELETE FROM terminology_profiles WHERE profile_id = 'profile-1'")


def test_clear_all_profile_selections_and_fail_closed_payload(storage) -> None:
    connection, repository = storage
    profile = _profile()
    repository.insert_profile(profile)
    repository.insert_published(_published(profile), expected_draft_revision=0)
    for variant_id in ("variant-a", "variant-b"):
        repository.set_selection(
            TerminologyProfileSelection(
                "project-1",
                variant_id,
                "profile-1",
                1,
                "2026-09-06T00:00:01+00:00",
            )
        )
    repository.clear_profile_selections("profile-1")
    assert repository.get_selection("project-1", "variant-a") is None
    assert repository.get_selection("project-1", "variant-b") is None

    connection.execute(
        "UPDATE terminology_profiles SET draft_json = ? WHERE profile_id = ?",
        ('{"schema_version":999,"value":{}}', "profile-1"),
    )
    with pytest.raises(ValueError, match="unsupported|invalid"):
        repository.get_profile("profile-1")
