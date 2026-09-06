from __future__ import annotations

from datetime import UTC, datetime

import pytest

from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    ProfileTermMapping,
    TerminologyProfileConflictError,
    TerminologyProfileContent,
    TerminologyProfileError,
    TerminologyProfileService,
)


def _service() -> tuple[TerminologyProfileService, InMemoryTerminologyProfileRepository]:
    repository = InMemoryTerminologyProfileRepository()
    service = TerminologyProfileService(
        repository,
        now=lambda: datetime(2026, 9, 6, tzinfo=UTC),
        new_id=lambda: "profile-1",
    )
    return service, repository


def test_profile_must_be_published_before_it_can_be_selected() -> None:
    service, _ = _service()
    profile = service.create("project-1", "大学汉化")

    with pytest.raises(TerminologyProfileError, match="published"):
        service.select("project-1", "variant-1", profile.profile_id)

    content = TerminologyProfileContent(
        mappings=(ProfileTermMapping("Whiterun", "白漫城", "雪漫城"),),
    )
    saved = service.save_draft(profile.profile_id, content, expected_revision=0)
    published = service.publish(profile.profile_id, expected_draft_revision=saved.draft_revision)
    selection = service.select("project-1", "variant-1", profile.profile_id)

    assert selection.revision == published.revision
    assert service.selected_revision("project-1", "variant-1") == published


def test_profile_can_be_created_with_a_complete_initial_draft() -> None:
    service, repository = _service()
    content = TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫城", "雪漫城"),))

    profile = service.create_with_content("project-1", "白漫方案", content)

    assert profile.draft == content
    assert profile.draft_revision == 0
    assert repository.get_profile(profile.profile_id) == profile


def test_published_revision_is_immutable_when_draft_changes() -> None:
    service, repository = _service()
    profile = service.create("project-1", "A")
    first_content = TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫"),))
    first_draft = service.save_draft(profile.profile_id, first_content, expected_revision=0)
    first = service.publish(profile.profile_id, expected_draft_revision=first_draft.draft_revision)

    second_content = TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫城", "雪漫"),))
    second_draft = service.save_draft(profile.profile_id, second_content, expected_revision=1)
    second = service.publish(profile.profile_id, expected_draft_revision=second_draft.draft_revision)

    assert repository.get_published(profile.profile_id, 1) == first
    assert second.revision == 2
    assert first.content.mappings[0].translation == "白漫"


def test_new_publish_advances_existing_variant_selection_for_new_runs() -> None:
    service, _ = _service()
    profile = service.create("project-1", "A")
    service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project-1", "variant-1", profile.profile_id)
    updated = service.save_draft(
        profile.profile_id,
        TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "白漫", "雪漫"),)),
        expected_revision=0,
    )

    revision = service.publish(profile.profile_id, expected_draft_revision=updated.draft_revision)

    assert revision.revision == 2
    assert service.selected_revision("project-1", "variant-1") == revision


def test_stale_draft_and_duplicate_names_are_rejected() -> None:
    service, _ = _service()
    profile = service.create("project-1", "官中")
    with pytest.raises(TerminologyProfileConflictError, match="already exists"):
        service.create("project-1", " 官中 ")

    content = TerminologyProfileContent(mappings=(ProfileTermMapping("Whiterun", "雪漫"),))
    service.save_draft(profile.profile_id, content, expected_revision=0)
    with pytest.raises(TerminologyProfileConflictError, match="changed"):
        service.save_draft(profile.profile_id, content, expected_revision=0)


def test_archiving_current_profile_clears_all_variant_selections() -> None:
    service, repository = _service()
    profile = service.create("project-1", "A")
    published = service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project-1", "variant-a", profile.profile_id)
    service.select("project-1", "variant-b", profile.profile_id)

    service.archive(profile.profile_id)

    assert repository.get_published(profile.profile_id, published.revision) == published
    assert service.selected_revision("project-1", "variant-a") is None
    assert service.selected_revision("project-1", "variant-b") is None


def test_selection_is_project_scoped() -> None:
    service, _ = _service()
    profile = service.create("project-1", "A")
    service.publish(profile.profile_id, expected_draft_revision=0)

    with pytest.raises(TerminologyProfileError, match="another Project"):
        service.select("project-2", "variant-1", profile.profile_id)
