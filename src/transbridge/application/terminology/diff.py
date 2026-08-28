"""Deterministic semantic diff for immutable terminology versions."""

from __future__ import annotations

from .identity import canonical_digest
from .models import (
    CanonicalChange,
    CanonicalDiff,
    ChangeType,
    ConflictGroup,
    ManualAction,
    TermDecision,
    TerminologyVersion,
)


class CanonicalDiffEngine:
    def compare(
        self,
        parent: TerminologyVersion | None,
        *,
        target_version_id: str,
        decisions: tuple[TermDecision, ...],
        conflicts: tuple[ConflictGroup, ...] = (),
        manual_actions: tuple[ManualAction, ...] = (),
    ) -> CanonicalDiff:
        before = {} if parent is None else {item.term_id: item for item in parent.decisions}
        after = {item.term_id: item for item in decisions}
        manual_ids = {
            identity
            for action in manual_actions
            for identity in (action.term_id, action.replacement_term_id)
            if identity is not None
        }
        changes: list[CanonicalChange] = []
        replacements = {item.term_id: item for item in decisions if item.replacement_of in before}
        for term_id in sorted(before.keys() | after.keys()):
            old = before.get(term_id)
            new = after.get(term_id)
            manual = term_id in manual_ids
            if old is None and new is not None:
                if new.term_id in replacements:
                    replaced = before[new.replacement_of]  # type: ignore[index]
                    replacement_type = (
                        ChangeType.SCOPE_CHANGED
                        if replaced.original == new.original and replaced.scope != new.scope
                        else ChangeType.ORIGINAL_REPLACED
                    )
                    changes.append(
                        self._change(
                            replacement_type,
                            term_id,
                            replaced,
                            new,
                            manual,
                            (("replacement_of", replaced.term_id),),
                        )
                    )
                else:
                    changes.append(
                        self._change(
                            ChangeType.SUPPRESSED if new.suppressed else ChangeType.ADDED, term_id, None, new, manual
                        )
                    )
                continue
            if old is not None and new is None:
                changes.append(self._change(ChangeType.SUPPRESSED, term_id, old, None, manual))
                continue
            assert old is not None and new is not None
            semantic_count = len(changes)
            if old.suppressed != new.suppressed:
                change_type = ChangeType.SUPPRESSED if new.suppressed else ChangeType.REENABLED
                changes.append(self._change(change_type, term_id, old, new, manual))
            if old.translation != new.translation:
                changes.append(self._change(ChangeType.TRANSLATION_CHANGED, term_id, old, new, manual))
            if old.scope != new.scope:
                changes.append(self._change(ChangeType.SCOPE_CHANGED, term_id, old, new, manual))
            attributes_before = (
                old.original,
                old.normalized_original,
                old.status,
                old.variants,
                old.notes,
                old.replacement_of,
            )
            attributes_after = (
                new.original,
                new.normalized_original,
                new.status,
                new.variants,
                new.notes,
                new.replacement_of,
            )
            if attributes_before != attributes_after:
                changes.append(self._change(ChangeType.ATTRIBUTES_CHANGED, term_id, old, new, manual))
            if len(changes) == semantic_count and old.evidence_ids != new.evidence_ids:
                changes.append(self._change(ChangeType.EVIDENCE_ONLY, term_id, old, new, manual))

        old_conflicts = {} if parent is None else {item.conflict_group_id: item for item in parent.conflicts}
        new_conflicts = {item.conflict_group_id: item for item in conflicts}
        for identity in sorted(old_conflicts.keys() | new_conflicts.keys()):
            old = old_conflicts.get(identity)
            new = new_conflicts.get(identity)
            old_status = "absent" if old is None else old.status.value
            new_status = "absent" if new is None else new.status.value
            if old_status != new_status:
                changes.append(
                    self._change(
                        ChangeType.CONFLICT_STATUS_CHANGED,
                        identity,
                        None,
                        None,
                        False,
                        (("before_status", old_status), ("after_status", new_status)),
                    )
                )

        parent_id = None if parent is None else parent.ref.version_id
        ordered = tuple(sorted(changes, key=lambda item: item.change_id))
        digest = canonical_digest(
            {"parent": parent_id, "target": target_version_id, "changes": ordered},
            namespace="terminology.canonical-diff.v1",
        )
        return CanonicalDiff(parent_id, target_version_id, digest, ordered)

    @staticmethod
    def _change(
        change_type: ChangeType,
        term_id: str,
        before: TermDecision | None,
        after: TermDecision | None,
        manual: bool,
        details: tuple[tuple[str, str], ...] = (),
    ) -> CanonicalChange:
        before_digest = _decision_digest(before)
        after_digest = _decision_digest(after)
        change_id = canonical_digest(
            {
                "type": change_type,
                "term": term_id,
                "before": before_digest,
                "after": after_digest,
                "details": details,
            },
            namespace="terminology.canonical-change.v1",
        )
        return CanonicalChange(
            change_id, change_type, term_id, before_digest, after_digest, manual, before, after, details
        )


def _decision_digest(decision: TermDecision | None) -> str | None:
    return None if decision is None else canonical_digest(decision, namespace="terminology.term-decision.v1")


__all__ = ["CanonicalDiffEngine"]
