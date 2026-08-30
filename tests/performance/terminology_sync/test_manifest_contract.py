from __future__ import annotations

import pytest

from .dataset import DIAGNOSTIC_PROFILES
from .manifest import DIAGNOSTIC_ONLY, TerminologySyncPerformanceManifest


def _manifest() -> TerminologySyncPerformanceManifest:
    return TerminologySyncPerformanceManifest(
        DIAGNOSTIC_PROFILES[0],
        (("python", "3.12"), ("runner", "controlled-ci")),
        (("remote_fetch", 12.5), ("planning", 8.25), ("state_write", 2.0)),
        120_000_000,
        105_000_000,
        25.0,
    )


def test_profiles_and_manifest_are_deterministic_and_non_release_claiming() -> None:
    first = _manifest()
    second = _manifest()

    assert first.profile.dataset_digest == second.profile.dataset_digest
    assert first.to_json() == second.to_json()
    assert first.to_dict()["evidence_kind"] == DIAGNOSTIC_ONLY
    assert first.to_dict()["release_gate_eligible"] is False
    assert set(first.to_dict()["phase_timings_ms"]) == {"remote_fetch", "planning", "state_write"}


def test_manifest_rejects_formal_gate_claim_without_confirmed_thresholds() -> None:
    with pytest.raises(ValueError, match="cannot claim"):
        TerminologySyncPerformanceManifest(
            DIAGNOSTIC_PROFILES[1],
            (("runner", "controlled-ci"),),
            (("planning", 1.0),),
            None,
            None,
            release_gate_eligible=True,
        )
