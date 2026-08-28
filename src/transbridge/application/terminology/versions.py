"""Materialize immutable terminology version state from a reviewed draft."""

from __future__ import annotations

from dataclasses import dataclass

from .models import BuildResult, ConflictGroup, ManualAction, TermDecision, TerminologyDraft, TerminologyVersion


@dataclass(frozen=True, slots=True)
class VersionContent:
    decisions: tuple[TermDecision, ...]
    conflicts: tuple[ConflictGroup, ...]
    manual_actions: tuple[ManualAction, ...]


class VersionMaterializer:
    """Validate and freeze the semantic content used by a publication."""

    def materialize(
        self,
        build: BuildResult,
        *,
        draft: TerminologyDraft | None = None,
        rollback_source: TerminologyVersion | None = None,
    ) -> VersionContent:
        if (draft is None) == (rollback_source is None):
            raise ValueError("materialization requires exactly one draft or rollback source")
        if draft is not None:
            return self.from_draft(build, draft)
        assert rollback_source is not None
        return self.from_version(build, rollback_source)

    def from_draft(self, build: BuildResult, draft: TerminologyDraft) -> VersionContent:
        build.require_publishable()
        if (draft.ref.project_id, draft.ref.variant_id) != (build.project_id, build.variant_id):
            raise ValueError("draft and build must belong to the same Project/Variant")
        self._validate_decisions(draft.decisions, build.project_id, build.variant_id)
        resolutions = {item.conflict_group_id: item for item in draft.conflict_resolutions}
        conflicts = tuple(resolutions.get(item.conflict_group_id, item) for item in build.conflicts)
        unknown = set(resolutions).difference(item.conflict_group_id for item in build.conflicts)
        if unknown:
            raise ValueError("draft resolves a conflict that is absent from the pinned build")
        return VersionContent(draft.decisions, conflicts, draft.actions)

    def from_version(self, build: BuildResult, source: TerminologyVersion) -> VersionContent:
        """Create rollback content without mutating or reusing the historical version identity."""

        build.require_publishable()
        if (source.ref.project_id, source.ref.variant_id) != (build.project_id, build.variant_id):
            raise ValueError("rollback source and build must belong to the same Project/Variant")
        self._validate_decisions(source.decisions, build.project_id, build.variant_id)
        return VersionContent(source.decisions, source.conflicts, ())

    @staticmethod
    def _validate_decisions(decisions: tuple[TermDecision, ...], project_id: str, variant_id: str) -> None:
        if len({item.term_id for item in decisions}) != len(decisions):
            raise ValueError("published decisions must have unique term IDs")
        for decision in decisions:
            if (decision.project_id, decision.variant_id) != (project_id, variant_id):
                raise ValueError("published decisions must belong to the build Project/Variant")
            if not decision.suppressed:
                decision.require_effective()


__all__ = ["VersionContent", "VersionMaterializer"]
