"""Application use case for profile lifecycle and atomic selection."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import uuid

from .models import (
    ProfileState,
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)
from .ports import TerminologyProfileRepository


class TerminologyProfileError(RuntimeError):
    pass


class TerminologyProfileConflictError(TerminologyProfileError):
    pass


class TerminologyProfileService:
    def __init__(
        self,
        repository: TerminologyProfileRepository,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._now = now or (lambda: datetime.now(UTC))
        self._new_id = new_id or (lambda: uuid.uuid4().hex)

    def list_profiles(self, project_id: str, *, include_archived: bool = False) -> tuple[TerminologyProfile, ...]:
        return self._repository.list_profiles(project_id, include_archived=include_archived)

    def create(self, project_id: str, name: str, *, copy_from: str | None = None) -> TerminologyProfile:
        content = TerminologyProfileContent()
        if copy_from is not None:
            source = self._require_profile(copy_from)
            if source.project_id != project_id:
                raise TerminologyProfileError("cannot copy a profile from another Project")
            content = source.draft
        return self.create_with_content(project_id, name, content)

    def create_with_content(
        self,
        project_id: str,
        name: str,
        content: TerminologyProfileContent,
    ) -> TerminologyProfile:
        """Create a profile whose initial draft is already complete.

        Importers use this boundary so a populated profile is never exposed as
        a transient empty draft.  The first publish still uses draft revision
        zero, preserving the existing optimistic-concurrency contract.
        """

        if not isinstance(content, TerminologyProfileContent):
            raise TypeError("profile content must be TerminologyProfileContent")
        self._require_unique_name(project_id, name)
        now = self._timestamp()
        profile = TerminologyProfile(
            profile_id=self._new_id(),
            project_id=project_id,
            name=name,
            draft=content,
            created_at=now,
            updated_at=now,
        )
        try:
            self._repository.insert_profile(profile)
        except ValueError as exc:
            raise TerminologyProfileConflictError(f"profile name already exists: {name.strip()}") from exc
        return profile

    def rename(self, profile_id: str, name: str) -> TerminologyProfile:
        profile = self._require_profile(profile_id)
        self._require_unique_name(profile.project_id, name, excluding=profile_id)
        try:
            return self._repository.rename_profile(profile_id, name, updated_at=self._timestamp())
        except ValueError as exc:
            raise TerminologyProfileConflictError(f"profile name already exists: {name.strip()}") from exc

    def save_draft(
        self,
        profile_id: str,
        content: TerminologyProfileContent,
        *,
        expected_revision: int,
    ) -> TerminologyProfile:
        profile = self._require_profile(profile_id)
        if profile.state is ProfileState.ARCHIVED:
            raise TerminologyProfileError("archived profile cannot be edited")
        try:
            return self._repository.save_draft(
                profile_id,
                content,
                expected_revision=expected_revision,
                updated_at=self._timestamp(),
            )
        except ValueError as exc:
            raise TerminologyProfileConflictError("profile draft changed; reload before saving") from exc

    def publish(self, profile_id: str, *, expected_draft_revision: int) -> PublishedTerminologyProfile:
        profile = self._require_profile(profile_id)
        if profile.state is ProfileState.ARCHIVED:
            raise TerminologyProfileError("archived profile cannot be published")
        if profile.draft_revision != expected_draft_revision:
            raise TerminologyProfileConflictError("profile draft changed; reload before publishing")
        revision_number = (profile.latest_published_revision or 0) + 1
        revision = PublishedTerminologyProfile(
            profile_id=profile.profile_id,
            project_id=profile.project_id,
            revision=revision_number,
            name=profile.name,
            content_digest=profile.draft.content_digest,
            content=profile.draft,
            published_at=self._timestamp(),
        )
        try:
            self._repository.insert_published(
                revision,
                expected_draft_revision=expected_draft_revision,
            )
        except ValueError as exc:
            raise TerminologyProfileConflictError("profile was published concurrently; reload and retry") from exc
        return revision

    def archive(self, profile_id: str) -> TerminologyProfile:
        profile = self._require_profile(profile_id)
        archived = self._repository.set_archived(profile_id, archived=True, updated_at=self._timestamp())
        self._repository.clear_profile_selections(profile.profile_id)
        return archived

    def restore(self, profile_id: str) -> TerminologyProfile:
        self._require_profile(profile_id)
        return self._repository.set_archived(profile_id, archived=False, updated_at=self._timestamp())

    def select(self, project_id: str, variant_id: str, profile_id: str) -> TerminologyProfileSelection:
        profile = self._require_profile(profile_id)
        if profile.project_id != project_id:
            raise TerminologyProfileError("profile belongs to another Project")
        if profile.state is ProfileState.ARCHIVED:
            raise TerminologyProfileError("archived profile cannot be selected")
        if profile.latest_published_revision is None:
            raise TerminologyProfileError("profile must be published before selection")
        revision = self._repository.get_published(profile_id, profile.latest_published_revision)
        if revision is None:
            raise TerminologyProfileError("latest published profile revision is unavailable")
        selection = TerminologyProfileSelection(
            project_id,
            variant_id,
            profile_id,
            revision.revision,
            self._timestamp(),
        )
        self._repository.set_selection(selection)
        return selection

    def clear_selection(self, project_id: str, variant_id: str) -> None:
        self._repository.clear_selection(project_id, variant_id)

    def selected_revision(self, project_id: str, variant_id: str) -> PublishedTerminologyProfile | None:
        selection = self._repository.get_selection(project_id, variant_id)
        if selection is None:
            return None
        profile = self._repository.get_profile(selection.profile_id)
        if profile is None or profile.state is ProfileState.ARCHIVED or profile.project_id != project_id:
            return None
        return self._repository.get_published(selection.profile_id, selection.revision)

    def published_revision(self, profile_id: str, revision: int) -> PublishedTerminologyProfile | None:
        return self._repository.get_published(profile_id, revision)

    def _require_profile(self, profile_id: str) -> TerminologyProfile:
        profile = self._repository.get_profile(profile_id)
        if profile is None:
            raise TerminologyProfileError(f"terminology profile not found: {profile_id}")
        return profile

    def _require_unique_name(self, project_id: str, name: str, *, excluding: str | None = None) -> None:
        normalized = name.strip().casefold()
        if not normalized:
            raise ValueError("profile name must not be empty")
        if any(
            item.profile_id != excluding and item.name.casefold() == normalized
            for item in self._repository.list_profiles(project_id, include_archived=True)
        ):
            raise TerminologyProfileConflictError(f"profile name already exists: {name.strip()}")

    def _timestamp(self) -> str:
        return self._now().astimezone(UTC).isoformat()


__all__ = [
    "TerminologyProfileConflictError",
    "TerminologyProfileError",
    "TerminologyProfileService",
]
