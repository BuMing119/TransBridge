"""Deterministic in-memory profile repository for tests and composition fallbacks."""

from __future__ import annotations

from dataclasses import replace

from .models import (
    ProfileState,
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)


class InMemoryTerminologyProfileRepository:
    def __init__(self) -> None:
        self._profiles: dict[str, TerminologyProfile] = {}
        self._published: dict[tuple[str, int], PublishedTerminologyProfile] = {}
        self._selections: dict[tuple[str, str], TerminologyProfileSelection] = {}

    def list_profiles(self, project_id: str, *, include_archived: bool = False) -> tuple[TerminologyProfile, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._profiles.values()
                    if item.project_id == project_id and (include_archived or item.state is ProfileState.ACTIVE)
                ),
                key=lambda item: (item.name.casefold(), item.profile_id),
            )
        )

    def get_profile(self, profile_id: str) -> TerminologyProfile | None:
        return self._profiles.get(profile_id)

    def insert_profile(self, profile: TerminologyProfile) -> None:
        if profile.profile_id in self._profiles:
            raise ValueError("profile already exists")
        self._profiles[profile.profile_id] = profile

    def rename_profile(self, profile_id: str, name: str, *, updated_at: str) -> TerminologyProfile:
        profile = self._require(profile_id)
        updated = replace(profile, name=name, updated_at=updated_at)
        self._profiles[profile_id] = updated
        return updated

    def save_draft(
        self,
        profile_id: str,
        content: TerminologyProfileContent,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> TerminologyProfile:
        profile = self._require(profile_id)
        if profile.draft_revision != expected_revision:
            raise ValueError("stale draft revision")
        updated = replace(
            profile,
            draft=content,
            draft_revision=profile.draft_revision + 1,
            updated_at=updated_at,
        )
        self._profiles[profile_id] = updated
        return updated

    def set_archived(self, profile_id: str, *, archived: bool, updated_at: str) -> TerminologyProfile:
        profile = self._require(profile_id)
        updated = replace(
            profile,
            state=ProfileState.ARCHIVED if archived else ProfileState.ACTIVE,
            updated_at=updated_at,
        )
        self._profiles[profile_id] = updated
        return updated

    def insert_published(
        self,
        revision: PublishedTerminologyProfile,
        *,
        expected_draft_revision: int,
    ) -> None:
        key = (revision.profile_id, revision.revision)
        profile = self._require(revision.profile_id)
        expected = (profile.latest_published_revision or 0) + 1
        if key in self._published or revision.revision != expected:
            raise ValueError("published revision already exists or is not next")
        if revision.project_id != profile.project_id:
            raise ValueError("published revision belongs to another Project")
        if profile.state is ProfileState.ARCHIVED:
            raise ValueError("archived profile cannot be published")
        if profile.draft_revision != expected_draft_revision or profile.draft.content_digest != revision.content_digest:
            raise ValueError("profile draft changed before publish")
        self._published[key] = revision
        self._profiles[profile.profile_id] = replace(
            profile,
            latest_published_revision=revision.revision,
            updated_at=revision.published_at,
        )
        for selection_key, selection in tuple(self._selections.items()):
            if selection.profile_id == revision.profile_id:
                self._selections[selection_key] = replace(
                    selection,
                    revision=revision.revision,
                    selected_at=revision.published_at,
                )

    def get_published(self, profile_id: str, revision: int) -> PublishedTerminologyProfile | None:
        return self._published.get((profile_id, revision))

    def get_selection(self, project_id: str, variant_id: str) -> TerminologyProfileSelection | None:
        return self._selections.get((project_id, variant_id))

    def set_selection(self, selection: TerminologyProfileSelection) -> None:
        revision = self.get_published(selection.profile_id, selection.revision)
        profile = self.get_profile(selection.profile_id)
        if (
            revision is None
            or revision.project_id != selection.project_id
            or profile is None
            or profile.state is ProfileState.ARCHIVED
            or profile.latest_published_revision != selection.revision
        ):
            raise ValueError("selected profile revision is unavailable")
        self._selections[(selection.project_id, selection.variant_id)] = selection

    def clear_selection(self, project_id: str, variant_id: str) -> None:
        self._selections.pop((project_id, variant_id), None)

    def clear_profile_selections(self, profile_id: str) -> None:
        for key in tuple(self._selections):
            if self._selections[key].profile_id == profile_id:
                del self._selections[key]

    def _require(self, profile_id: str) -> TerminologyProfile:
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError("profile not found")
        return profile


__all__ = ["InMemoryTerminologyProfileRepository"]
