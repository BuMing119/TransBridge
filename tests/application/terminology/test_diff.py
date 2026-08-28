from dataclasses import replace

from tests.application.terminology.story08_support import build
from transbridge.application.terminology.diff import CanonicalDiffEngine
from transbridge.application.terminology.models import (
    BuildCompleteness,
    CanonicalDiff,
    ChangeType,
    ConflictGroup,
    ConflictStatus,
    ConflictVariant,
    DecisionStatus,
    TermDecision,
    TerminologyVersion,
    TerminologyVersionRef,
    TermScope,
)


def _term(identity: str, *, original: str | None = None, translation: str = "译名", **changes) -> TermDecision:
    value = TermDecision(
        identity,
        "project-1",
        "variant-1",
        original or identity,
        (original or identity).lower(),
        translation,
        status=DecisionStatus.ADOPTED,
        evidence_ids=("e1",),
    )
    return replace(value, **changes)


def _conflict(status: ConflictStatus) -> ConflictGroup:
    return ConflictGroup(
        "conflict-1",
        "project-1",
        "variant-1",
        "dragon",
        (ConflictVariant("龙", ("c1",), ("e1",)), ConflictVariant("巨龙", ("c2",), ("e2",))),
        status=status,
        recommended_translation="龙" if status is ConflictStatus.UNIFIED else None,
    )


def test_canonical_diff_covers_every_typed_change_deterministically() -> None:
    source = build()
    before = (
        _term("removed"),
        _term("reenabled", suppressed=True),
        _term("translation"),
        _term("scope"),
        _term("attributes"),
        _term("evidence"),
        _term("old-name", original="Old Name"),
    )
    parent_ref = TerminologyVersionRef("v1", "project-1", "variant-1", "v1-content")
    parent = TerminologyVersion(
        parent_ref,
        None,
        source.ref,
        1,
        1,
        BuildCompleteness.FULL,
        "2026-08-28T00:00:00Z",
        before,
        CanonicalDiff(None, "v1", "diff-v1", ()),
        conflicts=(_conflict(ConflictStatus.UNRESOLVED),),
    )
    after = (
        replace(before[1], suppressed=False),
        replace(before[2], translation="新译名"),
        replace(before[3], scope=TermScope.plugin("Plugin.esm")),
        replace(before[4], notes="note"),
        replace(before[5], evidence_ids=("e2",)),
        replace(before[6], suppressed=True),
        _term("new-name", original="New Name", replacement_of="old-name"),
        _term("added"),
    )
    engine = CanonicalDiffEngine()
    first = engine.compare(
        parent,
        target_version_id="v2",
        decisions=after,
        conflicts=(_conflict(ConflictStatus.UNIFIED),),
    )
    second = engine.compare(
        parent,
        target_version_id="v2",
        decisions=tuple(reversed(after)),
        conflicts=(_conflict(ConflictStatus.UNIFIED),),
    )

    assert first == second
    assert {change.change_type for change in first.changes} == set(ChangeType)
    assert first.content_digest.startswith("terminology.canonical-diff.v1:")
