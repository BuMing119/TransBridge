# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""One complete terminology projection policy shared by preview and write paths."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
)
from transbridge.converter.translation_entry import TranslationEntry

from .models import PublishedTerminologyProfile
from .projection import ProjectedTranslation, ProjectionDiagnostic, SourceTermIndex, TerminologyProfileProjector
from .transition_projection import base_source_content, same_profile_revision, transition_content


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
        profile: PublishedTerminologyProfile | None,
        base_snapshot: EffectiveTerminologySnapshot | None,
        *,
        projector: TerminologyProfileProjector | None = None,
        source_profile: PublishedTerminologyProfile | None = None,
    ) -> None:
        self._profile = profile
        self._base = base_snapshot
        self._source_profile = source_profile
        self._same_revision = same_profile_revision(source_profile, profile)
        if self._same_revision:
            return
        if source_profile is not None:
            self._projector = (projector or TerminologyProfileProjector()).prepare(
                transition_content(source_profile, profile, base_snapshot), ignore_missing=True
            )
            return
        assert profile is not None
        self._projector = (projector or TerminologyProfileProjector()).prepare(
            base_source_content(profile, base_snapshot),
            ignore_missing=base_snapshot is not None and base_snapshot.status is EffectiveSnapshotStatus.READY,
        )
        self._owned_keys = frozenset(item.term_key for item in profile.content.mappings if item.scheme_owned)
        self._owned_sources = SourceTermIndex(item.original for item in profile.content.mappings if item.scheme_owned)

    def project(self, entry: TranslationEntry) -> ProjectedTranslation:
        return self.project_input(ProfileEntryInput.capture(entry))

    def project_input(self, entry: ProfileEntryInput) -> ProjectedTranslation:
        if self._same_revision or not entry.common_translation:
            return ProjectedTranslation(entry.common_translation, entry.common_translation)
        plugin_id = entry.plugin_id
        result = self._projector.project(
            entry_key=entry.entry_key,
            original=entry.original,
            common_translation=entry.common_translation,
            plugin_id=plugin_id,
        )
        if self._source_profile is not None:
            return result
        # A complete, explicit entry override needs no automatic term recognition
        # or base mapping evidence. Both consumers must honor that same priority.
        if result.used_override:
            return result
        base = self._base
        assert self._profile is not None
        if (
            base is not None
            and base.status is EffectiveSnapshotStatus.NO_PROJECT_VERSION
            and base.local_project_id == self._profile.project_id
            and (self._owned_sources.matching_originals(entry.original) or result.changed_term_keys)
            and set(result.changed_term_keys) <= self._owned_keys
        ):
            # Source-owned terms need no base membership. The pure projector
            # still requires historical text or a valid explicit binding, and
            # already preserves the complete entry whenever evidence is missing.
            return result
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
            diagnostics = result.diagnostics
        if diagnostics:
            return ProjectedTranslation(entry.common_translation, entry.common_translation, diagnostics=diagnostics)
        return result


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
