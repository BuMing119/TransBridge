from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from transbridge.application.terminology.budgets import (
    FR516_BUDGETS,
    FR516_BUDGETS_BY_REQUIREMENT,
    BudgetLevel,
)
from transbridge.application.terminology.feature_gates import (
    TERMINOLOGY_FEATURE_STAGE_ORDER,
    GateCheck,
    GateCheckStatus,
    TerminologyFeatureGateEvaluator,
    TerminologyFeatureStage,
    TerminologyReleaseEvidence,
    TerminologyReleaseEvidenceLoader,
)

pytestmark = pytest.mark.integration


def _all_check_ids() -> tuple[str, ...]:
    evaluator = TerminologyFeatureGateEvaluator()
    return tuple(
        blocker.rsplit(":", 1)[0]
        for gate in evaluator.evaluate()
        for blocker in gate.blockers
        if not blocker.startswith("previous-stage-disabled:")
    )


def test_machine_readable_budget_table_covers_every_confirmed_requirement() -> None:
    assert tuple(FR516_BUDGETS_BY_REQUIREMENT) == tuple(f"FR5.16.{index}" for index in range(33, 41))
    assert all(FR516_BUDGETS_BY_REQUIREMENT.values())
    assert len({item.check_id for item in FR516_BUDGETS}) == len(FR516_BUDGETS)
    assert (
        next(item for item in FR516_BUDGETS if item.metric == "local-build" and item.profile == "regular").limit == 90
    )
    incremental = next(item for item in FR516_BUDGETS if item.metric == "incremental-vs-full")
    assert (incremental.level, incremental.limit, incremental.unit) == (BudgetLevel.SHOULD, 30, "percent")


def test_release_gates_are_fail_closed_by_default_and_keep_the_confirmed_order() -> None:
    gates = TerminologyFeatureGateEvaluator().evaluate()

    assert tuple(item.stage for item in gates) == TERMINOLOGY_FEATURE_STAGE_ORDER
    assert not any(item.enabled for item in gates)
    assert "reference-environment-calibrated:not-run" in gates[0].blockers


def test_failure_in_an_early_stage_blocks_all_later_stages_even_when_their_checks_pass() -> None:
    evidence = TerminologyReleaseEvidence(
        tuple(
            GateCheck(
                check_id, GateCheckStatus.FAILED if check_id == "stress-benchmark-complete" else GateCheckStatus.PASSED
            )
            for check_id in _all_check_ids()
        )
    )

    gates = TerminologyFeatureGateEvaluator().evaluate(evidence)

    assert gates[0].blockers == ("stress-benchmark-complete:failed",)
    assert not any(item.enabled for item in gates)
    assert gates[1].blockers[0] == "previous-stage-disabled:analysis-report"


def test_supported_stages_enable_only_when_every_required_validation_passes() -> None:
    evidence = TerminologyReleaseEvidence(
        tuple(GateCheck(check_id, GateCheckStatus.PASSED) for check_id in _all_check_ids())
    )

    gates = TerminologyFeatureGateEvaluator().evaluate(evidence)

    assert all(item.enabled for item in gates[:-1])
    assert gates[-1].enabled is False
    assert gates[-1].stage is TerminologyFeatureStage.PARTIAL_PUBLISH
    assert gates[-1].blockers == ("stage-policy-disabled:partial-publish-not-supported",)
    assert TerminologyFeatureGateEvaluator().capabilities(evidence) == {
        **{stage.value: True for stage in TERMINOLOGY_FEATURE_STAGE_ORDER[:-1]},
        TerminologyFeatureStage.PARTIAL_PUBLISH.value: False,
    }


def test_duplicate_evidence_is_rejected_instead_of_using_last_write_wins() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        TerminologyReleaseEvidence((
            GateCheck("same", GateCheckStatus.PASSED),
            GateCheck("same", GateCheckStatus.FAILED),
        ))


def test_release_evidence_loader_is_fail_closed_for_missing_or_tampered_files(tmp_path: Path) -> None:
    loader = TerminologyReleaseEvidenceLoader()

    missing = loader.load(tmp_path / "missing.json")
    assert missing.evidence.checks == ()
    assert not any(item.enabled for item in TerminologyFeatureGateEvaluator().evaluate(missing.evidence))

    target = tmp_path / "evidence.json"
    target.write_text(
        json.dumps({
            "schema_version": 1,
            "checks": [{"check_id": "reference-environment-calibrated", "status": "passed"}],
            "artifact_digest": "0" * 64,
        }),
        encoding="utf-8",
    )
    tampered = loader.load(target)
    assert tampered.evidence.checks == ()
    assert tampered.diagnostics == ("release-evidence-invalid:ValueError",)


def test_release_evidence_loader_accepts_only_digest_bound_known_checks(tmp_path: Path) -> None:
    payload = {
        "schema_version": 1,
        "checks": [
            {
                "check_id": "reference-environment-calibrated",
                "status": "passed",
                "detail": "win11-ref-01",
            }
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    target = tmp_path / "evidence.json"
    target.write_text(
        json.dumps({**payload, "artifact_digest": hashlib.sha256(encoded).hexdigest()}),
        encoding="utf-8",
    )

    loaded = TerminologyReleaseEvidenceLoader().load(target)

    assert loaded.diagnostics == ()
    assert loaded.evidence.status("reference-environment-calibrated") is GateCheckStatus.PASSED
    assert not any(item.enabled for item in TerminologyFeatureGateEvaluator().evaluate(loaded.evidence))
