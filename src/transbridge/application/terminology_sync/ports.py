"""Persistence port for project-isolated terminology synchronization state."""

from __future__ import annotations

from typing import Protocol

from transbridge.application.terminology.ports import Page, PageRequest

from .models import (
    TerminologySyncBaseline,
    TerminologySyncCommit,
    TerminologySyncItemLink,
    TerminologySyncItemOutcomeRecord,
    TerminologySyncLine,
    TerminologySyncLineState,
    TerminologySyncProfile,
    TerminologySyncTarget,
    TerminologySyncTargetBinding,
)


class TerminologySyncTargetBindingPort(Protocol):
    """Resolve the current persisted binding without using a captured request."""

    def resolve_target_binding(self, project_id: str) -> TerminologySyncTargetBinding | None: ...


class TerminologySyncStatePort(Protocol):
    def resolve_line(
        self,
        project_id: str,
        variant_id: str,
        target: TerminologySyncTarget,
    ) -> TerminologySyncLineState: ...

    def activate_line(
        self,
        line: TerminologySyncLine,
        profile: TerminologySyncProfile,
    ) -> TerminologySyncLineState: ...

    def replace_active_variant_mapping(
        self,
        line: TerminologySyncLine,
        profile: TerminologySyncProfile,
        *,
        expected_mapping_revision: int,
        retired_at: str,
    ) -> TerminologySyncLineState: ...

    def update_profile(
        self,
        profile: TerminologySyncProfile,
        *,
        expected_revision: int,
    ) -> TerminologySyncProfile: ...

    def get_baseline(self, line_id: str) -> TerminologySyncBaseline | None: ...

    def list_item_links(
        self,
        line_id: str,
        request: PageRequest = PageRequest(),
    ) -> Page[TerminologySyncItemLink]: ...

    def list_outcomes(
        self,
        run_id: str,
        request: PageRequest = PageRequest(),
    ) -> Page[TerminologySyncItemOutcomeRecord]: ...

    def commit_run(
        self,
        commit: TerminologySyncCommit,
        *,
        expected_baseline_revision: int | None,
    ) -> TerminologySyncBaseline: ...


__all__ = ["TerminologySyncStatePort", "TerminologySyncTargetBindingPort"]
