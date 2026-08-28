"""Deterministic, user-facing projection of the canonical terminology diff."""

from __future__ import annotations

from dataclasses import dataclass

from .identity import canonical_digest
from .models import (
    CanonicalChange,
    CanonicalDiff,
    ChangeLogDocument,
    ChangeLogDocumentRef,
    ChangeType,
    ConflictGroup,
    ManualAction,
    TermDecision,
    TerminologyVersionRef,
)


@dataclass(frozen=True, slots=True)
class NarrativeTemplate:
    locale: str = "zh-CN"
    schema_version: str = "terminology-changelog.v1"
    version: str = "1"

    @property
    def digest(self) -> str:
        return canonical_digest(self, namespace="terminology.changelog-template.v1")


class ChangeNarrativeProjector:
    def __init__(self, template: NarrativeTemplate = NarrativeTemplate()) -> None:
        self._template = template

    def project(
        self,
        *,
        version_ref: TerminologyVersionRef,
        diff: CanonicalDiff,
        decisions: tuple[TermDecision, ...],
        conflicts: tuple[ConflictGroup, ...],
        manual_actions: tuple[ManualAction, ...],
        diagnostics: tuple[str, ...] = (),
    ) -> ChangeLogDocument:
        messages = tuple(self._message(change) for change in diff.changes)
        conflict_group_ids = tuple(item.conflict_group_id for item in conflicts)
        no_evidence_term_ids = tuple(item.term_id for item in decisions if not item.evidence_ids)
        manual_action_ids = tuple(item.action_id for item in manual_actions)
        payload = {
            "version": version_ref,
            "locale": self._template.locale,
            "schema": self._template.schema_version,
            "template": self._template.digest,
            "messages": messages,
            "changes": diff.changes,
            "diagnostics": diagnostics,
            "conflict_group_ids": conflict_group_ids,
            "no_evidence_term_ids": no_evidence_term_ids,
            "manual_action_ids": manual_action_ids,
        }
        digest = canonical_digest(payload, namespace="terminology.changelog-document.v1")
        ref = ChangeLogDocumentRef(f"changelog:{version_ref.variant_id}:{version_ref.version_id}", digest)
        return ChangeLogDocument(
            ref,
            version_ref,
            self._template.locale,
            self._template.schema_version,
            self._template.digest,
            messages,
            diff.changes,
            diagnostics,
            conflict_group_ids,
            no_evidence_term_ids,
            manual_action_ids,
        )

    @staticmethod
    def _message(change: CanonicalChange) -> tuple[str, tuple[str, ...]]:
        before = change.before
        after = change.after
        original = after.original if after is not None else before.original if before is not None else "术语冲突"
        translation = after.translation if after is not None else before.translation if before is not None else ""
        keys = {
            ChangeType.ADDED: "新增统一译名",
            ChangeType.SUPPRESSED: "不再推荐使用",
            ChangeType.TRANSLATION_CHANGED: "调整推荐译名",
            ChangeType.ORIGINAL_REPLACED: "替换术语原名",
            ChangeType.SCOPE_CHANGED: "调整使用范围",
            ChangeType.ATTRIBUTES_CHANGED: "更新术语属性",
            ChangeType.CONFLICT_STATUS_CHANGED: "更新异译处理状态",
            ChangeType.REENABLED: "恢复推荐使用",
            ChangeType.EVIDENCE_ONLY: "更新术语证据",
        }
        return keys[change.change_type], (original, translation)


__all__ = ["ChangeNarrativeProjector", "NarrativeTemplate"]
