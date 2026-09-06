"""Frozen write projection that never mutates the common entry collection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    TerminologyLookupContext,
    resolve_snapshot,
)
from transbridge.converter.translation_entry import TranslationEntry

from .models import PublishedTerminologyProfile, logical_term_key
from .projection import ProjectionDiagnostic, TerminologyProfileProjector, source_contains
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
        base = self.base_snapshot
        if base is None or base.status is not EffectiveSnapshotStatus.READY:
            diagnostics.append(
                ProjectionDiagnostic(
                    "base_terminology_snapshot_unavailable",
                    "无法读取基础术语快照，不能证明当前译名方案是否完整。",
                    entries[0].identity.serialize() if entries else "write-request",
                )
            )
        for entry in entries:
            if base is None or base.status is not EffectiveSnapshotStatus.READY:
                output.append(entry)
                continue
            plugin_id = _plugin_id(entry)
            result = self.projector.project(
                entry_key=entry.identity.serialize(),
                original=entry.original,
                common_translation=entry.translation,
                content=self.profile.content,
                plugin_id=plugin_id,
            )
            entry_diagnostics = result.diagnostics + self._unmapped_diagnostics(entry, plugin_id, base)
            projected_translation = entry.translation if entry_diagnostics else result.translation
            output.append(replace(entry, translation=projected_translation))
            diagnostics.extend(entry_diagnostics)
        return tuple(output), tuple(diagnostics)

    def _unmapped_diagnostics(
        self,
        entry: TranslationEntry,
        plugin_id: str | None,
        base: EffectiveTerminologySnapshot,
    ) -> tuple[ProjectionDiagnostic, ...]:
        profile = self.profile
        if profile is None:
            return ()
        mapped_keys = {item.term_key for item in profile.content.mappings}
        context = TerminologyLookupContext(self.project_id, self.variant_id, plugin_id=plugin_id)
        output = []
        seen: set[str] = set()
        for candidate in base.decisions:
            if not source_contains(entry.original, candidate.original):
                continue
            resolution = resolve_snapshot(base, candidate.original, context)
            decision = resolution.decision
            if decision is None:
                continue
            key = logical_term_key(
                decision.original,
                scope_kind=decision.scope.kind.value,
                plugin_id=decision.scope.plugin_id,
            )
            if key in mapped_keys or key in seen:
                continue
            seen.add(key)
            output.append(
                ProjectionDiagnostic(
                    "profile_mapping_missing",
                    f"当前译名方案缺少 {decision.original!r} 的映射，已保留整条项目译文。",
                    entry.identity.serialize(),
                    key,
                )
            )
        return tuple(output)


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


def _plugin_id(entry: TranslationEntry) -> str | None:
    form_id = entry.form_id_with_plugin
    if isinstance(form_id, str) and "|" in form_id:
        value = form_id.rsplit("|", 1)[1].strip()
        return value or None
    try:
        value = dict(entry.metadata).get("terminology_plugin_id")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, str) and value.strip() else None


__all__ = ["FrozenProfileWriteProjection", "TerminologyProfileWriteProjectionSource"]
