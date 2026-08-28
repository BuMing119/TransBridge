"""Project-level bilingual corpus assembly from registered source fragments."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects.source_registry import SourceRelation
from transbridge.persistence.v2.variant import VariantEntryState, VariantSnapshot

from .identity import canonical_digest, evidence_id
from .models import BilingualEvidence


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    entry_key: EntryKey
    original: str
    translation: str
    stage: Stage
    context: str = ""
    from_current_variant: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.original, str) or not isinstance(self.translation, str):
            raise TypeError("corpus text must be strings")
        stage = Stage.from_value(self.stage)
        if stage is None:
            raise ValueError("corpus entry stage is invalid")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "context", str(self.context or ""))

    @classmethod
    def from_parsed(cls, value: Any) -> CorpusEntry:
        identity = getattr(value, "identity", getattr(value, "entry_key", None))
        if not isinstance(identity, EntryKey):
            raise TypeError("parsed terminology entries require a complete EntryKey")
        return cls(
            identity,
            str(getattr(value, "original", "")),
            str(getattr(value, "translation", "")),
            getattr(value, "stage", Stage.UNTRANSLATED.value),
            str(getattr(value, "context", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class SourceCorpusFragment:
    source_id: str
    format_id: str
    fingerprint: str
    entries: tuple[CorpusEntry, ...]
    plugin_scope: str | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.source_id, "source ID"),
            (self.format_id, "format ID"),
            (self.fingerprint, "source fingerprint"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must not be empty")
        entries = tuple(sorted(self.entries, key=lambda item: item.entry_key))
        keys = tuple(item.entry_key for item in entries)
        if len(keys) != len(set(keys)):
            raise ValueError("source corpus fragment contains duplicate EntryKeys")
        object.__setattr__(self, "entries", entries)

    @classmethod
    def from_parsed(
        cls,
        *,
        source_id: str,
        format_id: str,
        fingerprint: str,
        entries: tuple[Any, ...],
        plugin_scope: str | None = None,
    ) -> SourceCorpusFragment:
        return cls(
            source_id,
            format_id,
            fingerprint,
            tuple(CorpusEntry.from_parsed(item) for item in entries),
            plugin_scope,
        )


@dataclass(frozen=True, slots=True)
class EvidenceAssemblyResult:
    evidence: tuple[BilingualEvidence, ...]
    excluded_reasons: tuple[tuple[str, int], ...]
    diagnostics: tuple[str, ...]


class EvidenceEligibilityPolicy:
    """FR5.16 v1 eligibility: bilingual, visible, and not questionable."""

    version = "terminology.eligibility.v1"

    def exclusion_reason(self, *, original: str, translation: str, stage: Stage) -> str | None:
        if not original.strip():
            return "original_empty"
        if not translation.strip():
            return "translation_empty"
        if stage is Stage.HIDDEN:
            return "hidden"
        if stage is Stage.QUESTIONABLE:
            return "questionable"
        return None


class EvidenceAssembler:
    def __init__(self, policy: EvidenceEligibilityPolicy | None = None) -> None:
        self._policy = policy or EvidenceEligibilityPolicy()

    def assemble(
        self,
        *,
        project_id: str,
        variant_id: str,
        fragments: tuple[SourceCorpusFragment, ...],
        relations: tuple[SourceRelation, ...],
        variant_snapshot: VariantSnapshot,
    ) -> EvidenceAssemblyResult:
        fragment_map: dict[str, SourceCorpusFragment] = {}
        overlay_diagnostics: list[str] = []
        for item in fragments:
            overlay, item_diagnostics = _overlay_variant(item, variant_snapshot)
            fragment_map[item.source_id] = overlay
            overlay_diagnostics.extend(item_diagnostics)
        if len(fragment_map) != len(fragments):
            raise ValueError("corpus source IDs must be unique")
        ordered_relations = tuple(sorted(relations, key=lambda item: item.relation_id))
        outgoing = {item.from_source_id for item in ordered_relations}
        incoming = {item.to_source_id for item in ordered_relations}
        excluded: Counter[str] = Counter()
        diagnostics: list[str] = list(overlay_diagnostics)
        evidence: list[BilingualEvidence] = []

        for fragment in sorted(fragment_map.values(), key=lambda item: item.source_id):
            if fragment.source_id in outgoing:
                continue
            for entry in fragment.entries:
                if fragment.source_id in incoming and not entry.translation.strip():
                    continue
                item = self._make_evidence(
                    project_id=project_id,
                    variant_id=variant_id,
                    original_entry=entry,
                    translation=entry.translation,
                    translation_stage=entry.stage,
                    chain=(fragment,),
                    plugin_scope=fragment.plugin_scope,
                    from_current_variant=entry.from_current_variant,
                    excluded=excluded,
                )
                if item is not None:
                    evidence.append(item)

        for relation in ordered_relations:
            source = fragment_map.get(relation.from_source_id)
            target = fragment_map.get(relation.to_source_id)
            if source is None or target is None:
                excluded["relation_source_missing"] += 1
                diagnostics.append(f"RELATION_SOURCE_MISSING:{relation.relation_id}")
                continue
            if relation.alignment_policy != "entry_key":
                excluded["relation_policy_unsupported"] += 1
                diagnostics.append(f"RELATION_POLICY_UNSUPPORTED:{relation.relation_id}")
                continue
            target_by_key = {entry.entry_key: entry for entry in target.entries}
            target_by_local = _compatible_local_index(source, target)
            for translated in source.entries:
                original = target_by_key.get(translated.entry_key)
                if original is None and target_by_local is not None:
                    original = target_by_local.get(translated.entry_key.local_key)
                if original is None:
                    excluded["relation_entry_missing"] += 1
                    continue
                translation = translated.translation.strip() or translated.original.strip()
                stage = original.stage if original.from_current_variant else translated.stage
                if original.from_current_variant and original.translation.strip():
                    translation = original.translation
                item = self._make_evidence(
                    project_id=project_id,
                    variant_id=variant_id,
                    original_entry=original,
                    translation=translation,
                    translation_stage=stage,
                    chain=(source, target),
                    plugin_scope=target.plugin_scope or source.plugin_scope,
                    from_current_variant=original.from_current_variant or translated.from_current_variant,
                    excluded=excluded,
                )
                if item is not None:
                    evidence.append(item)

        deduplicated = {item.evidence_id: item for item in evidence}
        return EvidenceAssemblyResult(
            tuple(sorted(deduplicated.values(), key=lambda item: item.evidence_id)),
            tuple(sorted(excluded.items())),
            tuple(sorted(set(diagnostics))),
        )

    def _make_evidence(
        self,
        *,
        project_id: str,
        variant_id: str,
        original_entry: CorpusEntry,
        translation: str,
        translation_stage: Stage,
        chain: tuple[SourceCorpusFragment, ...],
        plugin_scope: str | None,
        from_current_variant: bool,
        excluded: Counter[str],
    ) -> BilingualEvidence | None:
        reason = self._policy.exclusion_reason(
            original=original_entry.original,
            translation=translation,
            stage=translation_stage,
        )
        if reason is not None:
            excluded[reason] += 1
            return None
        source_ids = tuple(item.source_id for item in chain)
        serialized_key = original_entry.entry_key.serialize()
        identity = evidence_id(
            project_id=project_id,
            variant_id=variant_id,
            source_chain=source_ids,
            entry_key=serialized_key,
            original=original_entry.original,
            translation=translation,
        )
        fingerprint = canonical_digest(
            {item.source_id: item.fingerprint for item in chain},
            namespace="terminology.source-chain-fingerprint.v1",
        )
        formats = "+".join(sorted({item.format_id for item in chain}))
        return BilingualEvidence(
            evidence_id=identity,
            project_id=project_id,
            variant_id=variant_id,
            source_chain=source_ids,
            namespace=original_entry.entry_key.namespace.value,
            entry_key=serialized_key,
            original=original_entry.original.strip(),
            translation=translation.strip(),
            source_format=formats,
            source_fingerprint=fingerprint,
            context=original_entry.context,
            stage=translation_stage.name.lower(),
            plugin_scope=plugin_scope,
            from_current_variant=from_current_variant,
        )


def _overlay_variant(
    fragment: SourceCorpusFragment, snapshot: VariantSnapshot
) -> tuple[SourceCorpusFragment, tuple[str, ...]]:
    states = {item.entry_key: item for item in snapshot.entries}
    fingerprints = {item.namespace: item.sha256 for item in snapshot.source_fingerprints}
    entries: list[CorpusEntry] = []
    diagnostics: list[str] = []
    for entry in fragment.entries:
        state = states.get(entry.entry_key)
        if state is not None and fingerprints.get(entry.entry_key.namespace) != fragment.fingerprint:
            diagnostics.append(f"VARIANT_FINGERPRINT_MISMATCH:{fragment.source_id}:{entry.entry_key.namespace.value}")
            state = None
        entries.append(_apply_variant(entry, state))
    return replace(fragment, entries=tuple(entries)), tuple(sorted(set(diagnostics)))


def _apply_variant(entry: CorpusEntry, state: VariantEntryState | None) -> CorpusEntry:
    if state is None or state.tombstone:
        return entry
    return replace(
        entry,
        translation=state.translation,
        stage=state.stage,
        from_current_variant=True,
    )


def _compatible_local_index(
    source: SourceCorpusFragment,
    target: SourceCorpusFragment,
) -> dict[str, CorpusEntry] | None:
    """Allow legacy local-key alignment only for an unambiguous namespace pair."""

    source_namespaces = {entry.entry_key.namespace for entry in source.entries}
    target_namespaces = {entry.entry_key.namespace for entry in target.entries}
    if len(source_namespaces) != 1 or len(target_namespaces) != 1:
        return None
    return {entry.entry_key.local_key: entry for entry in target.entries}


__all__ = [
    "CorpusEntry",
    "EvidenceAssembler",
    "EvidenceAssemblyResult",
    "EvidenceEligibilityPolicy",
    "SourceCorpusFragment",
]
