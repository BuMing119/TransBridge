from dataclasses import FrozenInstanceError

import pytest

from tests.application.translation.test_terminology_run_snapshot import _ready, _Source
from transbridge.application.terminology.effective import TerminologyLookupContext
from transbridge.application.translation.terminology_run_snapshot import (
    FrozenEffectiveTerminologyPort,
    FrozenTerminologyRunSnapshot,
    TerminologyRunSnapshotError,
    TerminologyRunSnapshotFactory,
)


def test_frozen_port_reuses_verified_snapshot_and_preserves_scope_validation(monkeypatch):
    frozen = TerminologyRunSnapshotFactory(_Source(_ready("v1", "d1"))).freeze("project-1", "variant-1")
    verifies = []
    original = FrozenTerminologyRunSnapshot.verify

    def track(self):
        verifies.append(self.ref)
        original(self)

    monkeypatch.setattr(FrozenTerminologyRunSnapshot, "verify", track)
    port = FrozenEffectiveTerminologyPort(frozen)
    first = port.snapshot("project-1", "variant-1", "v1")
    for _ in range(5):
        assert port.snapshot("project-1", "variant-1", "v1") is first
        assert port.resolve("Sword", TerminologyLookupContext("project-1", "variant-1")).decision.translation == "剑"
    assert len(verifies) == 1
    with pytest.raises(TerminologyRunSnapshotError, match="Project/Variant"):
        port.snapshot("project-2", "variant-1", "v1")
    with pytest.raises(TerminologyRunSnapshotError, match="does not match"):
        port.snapshot("project-1", "variant-1", "v2")
    with pytest.raises(FrozenInstanceError):
        first.decisions[0].translation = "变更"


def test_frozen_port_rejects_corruption_before_building_a_cache():
    frozen = TerminologyRunSnapshotFactory(_Source(_ready("v1", "d1"))).freeze("project-1", "variant-1")
    object.__setattr__(frozen, "decisions_digest", "corrupted")
    with pytest.raises(TerminologyRunSnapshotError, match="digest mismatch"):
        FrozenEffectiveTerminologyPort(frozen)
