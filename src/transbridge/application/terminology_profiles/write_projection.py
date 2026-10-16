"""Frozen write projection that never mutates the common entry collection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from transbridge.application.terminology.effective import EffectiveTerminologySnapshot
from transbridge.converter.translation_entry import TranslationEntry

from .entry_projection import ProfileEntryProjector
from .models import PublishedTerminologyProfile
from .projection import ProjectionDiagnostic, TerminologyProfileProjector
from .service import TerminologyProfileService


@dataclass(frozen=True, slots=True)
class FrozenProfileWriteProjection:
    project_id: str
    variant_id: str
    profile: PublishedTerminologyProfile | None
    projector: TerminologyProfileProjector
    base_snapshot: EffectiveTerminologySnapshot | None = None

    @property
    def identity(self) -> str:
        if self.profile is None:
            return "common-translation"
        base_version = "unavailable" if self.base_snapshot is None else self.base_snapshot.version_id
        base_digest = "unavailable" if self.base_snapshot is None else self.base_snapshot.content_digest
        return (
            f"terminology-profile:{self.profile.profile_id}:r{self.profile.revision}:"
            f"{self.profile.content_digest}:base:{base_version}:{base_digest}"
        )

    @property
    def metadata(self) -> tuple[tuple[str, str], ...]:
        if self.profile is None:
            return (("terminology_profile", "common-translation"),)
        values = [
            ("terminology_profile_id", self.profile.profile_id),
            ("terminology_profile_name", self.profile.name),
            ("terminology_profile_revision", str(self.profile.revision)),
            ("terminology_profile_digest", self.profile.content_digest),
        ]
        if self.base_snapshot is not None:
            values.extend((
                ("base_terminology_status", self.base_snapshot.status.value),
                ("base_terminology_version", self.base_snapshot.version_id or ""),
                ("base_terminology_digest", self.base_snapshot.content_digest or ""),
            ))
        return tuple(values)

    def project_entries(
        self,
        entries: tuple[TranslationEntry, ...],
    ) -> tuple[tuple[TranslationEntry, ...], tuple[ProjectionDiagnostic, ...]]:
        if self.profile is None:
            return entries, ()
        output = []
        diagnostics = []
        projector = ProfileEntryProjector(self.profile, self.base_snapshot, projector=self.projector)
        for entry in entries:
            result = projector.project(entry)
            output.append(replace(entry, translation=result.translation))
            diagnostics.extend(result.diagnostics)
        return tuple(output), tuple(diagnostics)


class TerminologyProfileWriteProjectionSource:
    def __init__(
        self,
        service_for_project: Callable[[str], TerminologyProfileService],
        *,
        projector: TerminologyProfileProjector | None = None,
        base_snapshot_for: Callable[[str, str], EffectiveTerminologySnapshot] | None = None,
    ) -> None:
        self._service_for_project = service_for_project
        self._projector = projector or TerminologyProfileProjector()
        self._base_snapshot_for = base_snapshot_for

    def freeze(self, project_id: str, variant_id: str) -> FrozenProfileWriteProjection:
        service = self._service_for_project(project_id)
        profile = service.selected_revision(project_id, variant_id)
        base = None
        if profile is not None and self._base_snapshot_for is not None:
            base = self._base_snapshot_for(project_id, variant_id)
        return FrozenProfileWriteProjection(
            project_id,
            variant_id,
            profile,
            self._projector,
            base,
        )


__all__ = ["FrozenProfileWriteProjection", "TerminologyProfileWriteProjectionSource"]
