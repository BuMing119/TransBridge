"""Storage boundary for terminology localization profiles."""

from __future__ import annotations

from typing import Protocol

from .models import (
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
)


class TerminologyProfileRepository(Protocol):
    def list_profiles(self, project_id: str, *, include_archived: bool = False) -> tuple[TerminologyProfile, ...]: ...

    def get_profile(self, profile_id: str) -> TerminologyProfile | None: ...

    def insert_profile(self, profile: TerminologyProfile) -> None: ...

    def rename_profile(self, profile_id: str, name: str, *, updated_at: str) -> TerminologyProfile: ...

    def save_draft(
        self,
        profile_id: str,
        content: TerminologyProfileContent,
        *,
        expected_revision: int,
        updated_at: str,
    ) -> TerminologyProfile: ...

    def set_archived(self, profile_id: str, *, archived: bool, updated_at: str) -> TerminologyProfile: ...

    def insert_published(
        self,
        revision: PublishedTerminologyProfile,
        *,
        expected_draft_revision: int,
    ) -> None: ...

    def get_published(self, profile_id: str, revision: int) -> PublishedTerminologyProfile | None: ...

    def get_selection(self, project_id: str, variant_id: str) -> TerminologyProfileSelection | None: ...

    def set_selection(self, selection: TerminologyProfileSelection) -> None: ...

    def clear_selection(self, project_id: str, variant_id: str) -> None: ...

    def clear_profile_selections(self, profile_id: str) -> None: ...


__all__ = ["TerminologyProfileRepository"]
