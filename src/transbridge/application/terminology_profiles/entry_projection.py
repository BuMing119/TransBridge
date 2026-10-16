"""One complete terminology projection policy shared by preview and write paths."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologyIndex,
    EffectiveTerminologySnapshot,
    TerminologyLookupContext,
)
from transbridge.converter.translation_entry import TranslationEntry

from .models import PublishedTerminologyProfile, logical_term_key
from .projection import ProjectedTranslation, ProjectionDiagnostic, SourceTermIndex, TerminologyProfileProjector


@dataclass(frozen=True, slots=True)
class ProfileEntryInput:
    """Only immutable text and scope cross the preview worker boundary."""

    entry_key: str
    original: str
    common_translation: str
    plugin_id: str | None

    @classmethod
    def capture(cls, entry: TranslationEntry) -> ProfileEntryInput:
        return cls(entry.identity.serialize(), entry.original, entry.translation, entry_plugin_id(entry))


class ProfileEntryProjector:
    """Apply a frozen profile, preserving complete entries whenever evidence is missing."""

    def __init__(
        self,
        profile: PublishedTerminologyProfile,
        base_snapshot: EffectiveTerminologySnapshot | None,
        *,
        projector: TerminologyProfileProjector | None = None,
    ) -> None:
        self._profile = profile
        self._base = base_snapshot
        self._projector = (projector or TerminologyProfileProjector()).prepare(profile.content)
        self._mapped_keys = frozenset(item.term_key for item in profile.content.mappings)
        self._base_index = None if base_snapshot is None else EffectiveTerminologyIndex(base_snapshot)
        self._base_sources = SourceTermIndex(() if self._base_index is None else self._base_index.originals)

    def project(self, entry: TranslationEntry) -> ProjectedTranslation:
        return self.project_input(ProfileEntryInput.capture(entry))

    def project_input(self, entry: ProfileEntryInput) -> ProjectedTranslation:
        plugin_id = entry.plugin_id
        result = self._projector.project(
            entry_key=entry.entry_key,
            original=entry.original,
            common_translation=entry.common_translation,
            plugin_id=plugin_id,
        )
        # A complete, explicit entry override needs no automatic term recognition
        # or base mapping evidence. Both consumers must honor that same priority.
        if result.used_override:
            return result
        base = self._base
        if (
            base is None
            or base.status is not EffectiveSnapshotStatus.READY
            or base.local_project_id != self._profile.project_id
        ):
            diagnostics = (
                ProjectionDiagnostic(
                    "base_terminology_snapshot_unavailable",
                    "无法读取基础术语快照，不能证明当前译名方案是否完整，已保留整条项目译文。",
                    entry.entry_key,
                ),
            )
        else:
            diagnostics = result.diagnostics + self._unmapped_diagnostics(entry, plugin_id, base)
        if diagnostics:
            return ProjectedTranslation(entry.common_translation, entry.common_translation, diagnostics=diagnostics)
        return result

    def _unmapped_diagnostics(
        self,
        entry: ProfileEntryInput,
        plugin_id: str | None,
        base: EffectiveTerminologySnapshot,
    ) -> tuple[ProjectionDiagnostic, ...]:
        context = TerminologyLookupContext(base.local_project_id, base.local_variant_id, plugin_id=plugin_id)
        index = self._base_index
        if index is None:
            return ()
        output = []
        seen: set[str] = set()
        for original in self._base_sources.matching_originals(entry.original):
            decision = index.resolve(original, context).decision
            if decision is None:
                continue
            key = logical_term_key(
                decision.original,
                scope_kind=decision.scope.kind.value,
                plugin_id=decision.scope.plugin_id,
            )
            if key in self._mapped_keys or key in seen:
                continue
            seen.add(key)
            output.append(
                ProjectionDiagnostic(
                    "profile_mapping_missing",
                    f"当前译名方案缺少 {decision.original!r} 的映射，已保留整条项目译文。",
                    entry.entry_key,
                    key,
                )
            )
        return tuple(output)


def entry_plugin_id(entry: TranslationEntry) -> str | None:
    """An explicit terminology scope overrides a plugin inferred from the record ID."""

    configured = dict(entry.metadata).get("terminology_plugin_id")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    form_id = entry.form_id_with_plugin
    if isinstance(form_id, str) and "|" in form_id:
        return form_id.rsplit("|", 1)[1].strip() or None
    return None


__all__ = ["ProfileEntryInput", "ProfileEntryProjector", "entry_plugin_id"]
