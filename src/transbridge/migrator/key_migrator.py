"""词条键对齐迁移：按 entry.key 将旧集合译文对齐到新集合同名键条目。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from transbridge.application.contracts import Diagnostic, DiagnosticSeverity
from transbridge.application.io import EntryKey, EntryRevision, Provenance, SourceNamespace
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.converter.translation_entry import (
    STAGE_TRANSLATED,
    _normalize_text,
)


class MigrationDisposition(StrEnum):
    EXACT = "exact"
    STALE = "stale"
    CONFLICT = "conflict"
    UNMATCHED = "unmatched"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    key: EntryKey
    original: str
    translation: str
    stage: int
    revision: EntryRevision = EntryRevision()
    provenance: tuple[Provenance, ...] = ()


@dataclass(frozen=True, slots=True)
class MigrationCandidate:
    target_key: EntryKey
    source_key: EntryKey
    translation: str
    before_revision: EntryRevision
    disposition: MigrationDisposition
    reasons: tuple[str, ...]
    provenance: tuple[Provenance, ...] = ()


@dataclass(frozen=True, slots=True)
class KeyMigrationPlan:
    candidates: tuple[MigrationCandidate, ...]
    unmatched: tuple[EntryKey, ...]
    conflicts: tuple[tuple[EntryKey, tuple[MigrationCandidate, ...]], ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    cancelled: bool = False

    @property
    def exact(self) -> tuple[MigrationCandidate, ...]:
        return tuple(item for item in self.candidates if item.disposition is MigrationDisposition.EXACT)


def plan_migration(
    old_entries,
    new_entries,
    *,
    old_fingerprint: str,
    new_fingerprint: str,
    namespace_mappings: tuple[tuple[SourceNamespace, SourceNamespace], ...] = (),
    cancellation: object | None = None,
) -> KeyMigrationPlan:
    """Build an immutable key migration plan without mutating either source."""
    if not old_fingerprint.strip() or not new_fingerprint.strip():
        raise ValueError("migration fingerprints must be explicit")
    sources = tuple(_migration_entry(item) for item in old_entries)
    targets = tuple(_migration_entry(item) for item in new_entries)
    allowed = {(old.value, new.value) for old, new in namespace_mappings}
    diagnostics: list[Diagnostic] = []
    candidates: list[MigrationCandidate] = []
    conflicts: list[tuple[EntryKey, tuple[MigrationCandidate, ...]]] = []
    unmatched: list[EntryKey] = []

    if _cancelled(cancellation):
        return KeyMigrationPlan(
            (),
            (),
            (),
            (Diagnostic("KEY_MIGRATION_CANCELLED", "Key migration planning was cancelled."),),
            True,
        )

    for target in targets:
        if _cancelled(cancellation):
            diagnostics.append(Diagnostic("KEY_MIGRATION_CANCELLED", "Key migration planning was cancelled."))
            return KeyMigrationPlan(tuple(candidates), tuple(unmatched), tuple(conflicts), tuple(diagnostics), True)
        if not DEFAULT_STAGE_POLICY.allows_tm_read(target.stage, target.translation, original=target.original):
            unmatched.append(target.key)
            continue
        matching = [
            source
            for source in sources
            if source.key.local_key == target.key.local_key
            and (
                source.key.namespace == target.key.namespace
                or (source.key.namespace.value, target.key.namespace.value) in allowed
            )
            and source.translation
            and DEFAULT_STAGE_POLICY.allows_tm_write(source.stage, source.translation, original=source.original)
        ]
        if not matching:
            unmatched.append(target.key)
            continue

        planned = tuple(
            _migration_candidate(
                source,
                target,
                fingerprint_changed=old_fingerprint != new_fingerprint,
            )
            for source in matching
        )
        translations = {item.translation for item in planned}
        if len(translations) > 1:
            conflicted = tuple(
                MigrationCandidate(
                    item.target_key,
                    item.source_key,
                    item.translation,
                    item.before_revision,
                    MigrationDisposition.CONFLICT,
                    (*item.reasons, "multiple_source_translations"),
                    item.provenance,
                )
                for item in planned
            )
            conflicts.append((target.key, conflicted))
            candidates.extend(conflicted)
            diagnostics.append(
                Diagnostic(
                    "KEY_MIGRATION_CONFLICT",
                    "Multiple mapped sources disagree for one target EntryKey.",
                    DiagnosticSeverity.WARNING,
                    details=(("entry_key", target.key.serialize()),),
                )
            )
        else:
            candidates.append(planned[0])

    if old_fingerprint != new_fingerprint and candidates:
        diagnostics.append(
            Diagnostic(
                "KEY_MIGRATION_SOURCE_FINGERPRINT_CHANGED",
                "Source fingerprint changed; inherited candidates are stale until confirmed.",
                DiagnosticSeverity.WARNING,
            )
        )
    return KeyMigrationPlan(tuple(candidates), tuple(unmatched), tuple(conflicts), tuple(diagnostics))


def _migration_entry(value) -> MigrationEntry:
    if isinstance(value, MigrationEntry):
        return value
    identity = getattr(value, "entry_key", None) or getattr(value, "identity", None)
    if callable(identity):
        identity = identity()
    if not isinstance(identity, EntryKey):
        local_key = str(getattr(value, "key", ""))
        identity = EntryKey(SourceNamespace.legacy(), local_key)
    revision = getattr(value, "revision", EntryRevision())
    if not isinstance(revision, EntryRevision):
        revision = EntryRevision(revision)
    return MigrationEntry(
        identity,
        str(getattr(value, "original", "")),
        str(getattr(value, "translation", "")),
        getattr(value, "stage", 0),
        revision,
        tuple(getattr(value, "provenance", ())),
    )


def _migration_candidate(
    source: MigrationEntry,
    target: MigrationEntry,
    *,
    fingerprint_changed: bool,
) -> MigrationCandidate:
    reasons: list[str] = []
    if _normalize_text(source.original) != _normalize_text(target.original):
        reasons.append("source_text_changed")
    if fingerprint_changed:
        reasons.append("source_fingerprint_changed")
    disposition = MigrationDisposition.STALE if reasons else MigrationDisposition.EXACT
    return MigrationCandidate(
        target.key,
        source.key,
        source.translation,
        target.revision,
        disposition,
        tuple(reasons) or ("entry_key_and_source_match",),
        source.provenance,
    )


def _cancelled(signal: object | None) -> bool:
    if signal is None:
        return False
    state = getattr(signal, "is_cancelled", None)
    if state is not None:
        return bool(state() if callable(state) else state)
    is_set = getattr(signal, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


@dataclass
class MigrationResult:
    inherited: int = 0  # 键命中且原文未变，直接继承
    needs_review: list = field(default_factory=list)  # 键命中但原文变化（entry.key 列表）
    missed: int = 0  # 键未命中，保留待翻译

    def to_dict(self) -> dict:
        return {
            "inherited": self.inherited,
            "needs_review": self.needs_review,
            "missed": self.missed,
        }


def migrate(old_collection, new_collection) -> MigrationResult:
    """按 entry.key 将旧集合译文对齐到新集合同名键条目。

    键命中且原文未变 → 继承译文（stage=已翻译）；
    键命中但原文变化 → 标记需复核（不套用）；
    键未命中 → 保留待翻译（count missed）。

    不修改旧集合，仅就地填充 new_collection 的译文。
    """
    if old_collection is None or new_collection is None:
        return MigrationResult()

    # 构建 old_collection 的 key → entry 映射
    old_by_key = {}
    for e in old_collection:
        if e.key:
            old_by_key.setdefault(e.key, e)

    result = MigrationResult()
    for e in new_collection:
        if not e.key or e.key not in old_by_key:
            result.missed += 1
            continue
        old = old_by_key[e.key]
        if not old.translation:
            result.missed += 1
            continue
        if _normalize_text(old.original) == _normalize_text(e.original):
            e.translation = old.translation
            e.stage = STAGE_TRANSLATED
            result.inherited += 1
        else:
            # 原文变化：标记需复核，不套用
            result.needs_review.append(e.key)
    return result
