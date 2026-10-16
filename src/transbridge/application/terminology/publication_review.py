"""Immutable publication review shared by GUI and production commands."""

from __future__ import annotations

from dataclasses import dataclass

from .models import BuildResultRef, CanonicalDiff, DraftRef, TerminologyVersion
from .publish import PublishTerminologyRequest
from .workloads import TerminologyExpectedState


@dataclass(frozen=True, slots=True)
class TerminologyPublishPreview:
    request: PublishTerminologyRequest
    candidate_version: TerminologyVersion
    config_json: str

    @property
    def diff(self) -> CanonicalDiff:
        return self.candidate_version.canonical_diff

    @property
    def expected(self) -> TerminologyExpectedState:
        return self.request.expected

    @property
    def build_ref(self) -> BuildResultRef:
        return self.request.build_ref

    @property
    def draft_ref(self) -> DraftRef:
        assert self.request.draft_ref is not None
        return self.request.draft_ref


__all__ = ["TerminologyPublishPreview"]
