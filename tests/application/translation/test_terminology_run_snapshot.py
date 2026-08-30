from __future__ import annotations

from datetime import UTC, datetime

import pytest

from transbridge.application.terminology.effective import (
    EffectiveSnapshotStatus,
    EffectiveTerminologySnapshot,
    TerminologyLookupContext,
)
from transbridge.application.terminology.identity import normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.translation.terminology_run_snapshot import (
    FrozenEffectiveTerminologyPort,
    TerminologyRunSnapshotError,
    TerminologyRunSnapshotFactory,
)


def _decision(translation: str = "剑") -> TermDecision:
    scope = TermScope.project()
    return TermDecision(
        term_id(project_id="project-1", variant_id="variant-1", scope=scope, original="Sword"),
        "project-1",
        "variant-1",
        "Sword",
        normalize_original("Sword"),
        translation,
        scope=scope,
        status=DecisionStatus.ADOPTED,
    )


def _ready(version: str, digest: str, translation: str = "剑") -> EffectiveTerminologySnapshot:
    return EffectiveTerminologySnapshot(
        "project-1",
        "variant-1",
        EffectiveSnapshotStatus.READY,
        version_id=version,
        content_digest=digest,
        decisions=(_decision(translation),),
    )


class _Source:
    def __init__(self, value: EffectiveTerminologySnapshot) -> None:
        self.value = value
        self.calls: list[str | None] = []

    def snapshot(self, project: str, variant: str, version_id: str | None = None):
        assert (project, variant) == ("project-1", "variant-1")
        self.calls.append(version_id)
        return self.value


def test_factory_freezes_one_verified_snapshot_and_port_never_follows_current() -> None:
    source = _Source(_ready("version-1", "digest-1", "旧剑"))
    frozen = TerminologyRunSnapshotFactory(
        source,
        now=lambda: datetime(2026, 8, 30, tzinfo=UTC),
    ).freeze("project-1", "variant-1")
    source.value = _ready("version-2", "digest-2", "新剑")
    port = FrozenEffectiveTerminologyPort(frozen)

    resolution = port.resolve(
        "Sword",
        TerminologyLookupContext("project-1", "variant-1", version_id="version-1"),
    )

    assert source.calls == [None]
    assert frozen.ref.snapshot_identity == "project-1:variant-1:version-1:digest-1"
    assert resolution.decision is not None
    assert resolution.decision.translation == "旧剑"
    with pytest.raises(TerminologyRunSnapshotError, match="does not match"):
        port.snapshot("project-1", "variant-1", "version-2")


def test_factory_preserves_explicit_no_project_version() -> None:
    source = _Source(EffectiveTerminologySnapshot("project-1", "variant-1", EffectiveSnapshotStatus.NO_PROJECT_VERSION))

    frozen = TerminologyRunSnapshotFactory(source).freeze("project-1", "variant-1")

    assert frozen.ref.status is EffectiveSnapshotStatus.NO_PROJECT_VERSION
    assert frozen.ref.version_id is None
    assert frozen.decisions == ()


@pytest.mark.parametrize("status", [EffectiveSnapshotStatus.CORRUPT, EffectiveSnapshotStatus.UNAVAILABLE])
def test_factory_rejects_unusable_project_snapshot(status: EffectiveSnapshotStatus) -> None:
    source = _Source(
        EffectiveTerminologySnapshot("project-1", "variant-1", status, diagnostics=("storage diagnostic",))
    )

    with pytest.raises(TerminologyRunSnapshotError, match=status.value):
        TerminologyRunSnapshotFactory(source).freeze("project-1", "variant-1")


def test_restore_requires_the_exact_version_and_content_digest() -> None:
    source = _Source(_ready("version-1", "digest-1"))
    factory = TerminologyRunSnapshotFactory(source)
    ref = factory.freeze("project-1", "variant-1").ref
    source.value = _ready("version-1", "changed-digest")

    with pytest.raises(TerminologyRunSnapshotError, match="content digest"):
        factory.restore(ref)

    assert source.calls == [None, "version-1"]
