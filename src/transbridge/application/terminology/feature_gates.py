"""Fail-closed release-candidate evidence evaluation for CI and release tooling."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final


class TerminologyFeatureStage(StrEnum):
    ANALYSIS_REPORT = "analysis-report"
    DRAFT_PUBLISH = "draft-publish"
    EFFECTIVE = "effective"
    HISTORY_REVERT_CHANGELOG = "history-revert-changelog"
    PARTIAL_PUBLISH = "partial-publish"


TERMINOLOGY_FEATURE_STAGE_ORDER: Final[tuple[TerminologyFeatureStage, ...]] = tuple(TerminologyFeatureStage)


class GateCheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not-run"


@dataclass(frozen=True, slots=True)
class GateCheck:
    check_id: str
    status: GateCheckStatus
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.check_id.strip():
            raise ValueError("gate check id must not be empty")


@dataclass(frozen=True, slots=True)
class TerminologyReleaseEvidence:
    checks: tuple[GateCheck, ...] = ()

    def __post_init__(self) -> None:
        ids = tuple(item.check_id for item in self.checks)
        if len(ids) != len(set(ids)):
            raise ValueError("gate evidence contains duplicate check ids")

    def status(self, check_id: str) -> GateCheckStatus:
        return next((item.status for item in self.checks if item.check_id == check_id), GateCheckStatus.NOT_RUN)


@dataclass(frozen=True, slots=True)
class TerminologyStageGate:
    stage: TerminologyFeatureStage
    enabled: bool
    blockers: tuple[str, ...] = ()


_COMMON_PERFORMANCE = (
    "reference-environment-calibrated",
    "regular-benchmark-complete",
    "stress-benchmark-complete",
    "fr516-shall-budgets-passed",
    "five-run-memory-stable",
    "cancel-response-passed",
    "incremental-digest-parity",
)
_REQUIREMENTS: Final[Mapping[TerminologyFeatureStage, tuple[str, ...]]] = MappingProxyType({
    TerminologyFeatureStage.ANALYSIS_REPORT: (
        *_COMMON_PERFORMANCE,
        "project-v3-copy-migration-passed",
        "sqlite-copy-migration-passed",
        "future-schema-read-only-passed",
        "corrupt-storage-preserved-passed",
        "no-wal-mode-passed",
        "crash-recovery-passed",
        "quality-report-passed",
    ),
    TerminologyFeatureStage.DRAFT_PUBLISH: ("publish-transaction-passed", "disk-full-safe-failure-passed"),
    TerminologyFeatureStage.EFFECTIVE: ("effective-loader-contract-passed",),
    TerminologyFeatureStage.HISTORY_REVERT_CHANGELOG: (
        "history-revert-passed",
        "changelog-parity-passed",
        "artifact-retry-passed",
    ),
    TerminologyFeatureStage.PARTIAL_PUBLISH: (),
})

_STAGE_POLICY_BLOCKERS: Final[Mapping[TerminologyFeatureStage, tuple[str, ...]]] = MappingProxyType({
    TerminologyFeatureStage.PARTIAL_PUBLISH: ("stage-policy-disabled:partial-publish-not-supported",),
})
TERMINOLOGY_GATE_CHECK_IDS: Final[frozenset[str]] = frozenset(
    check_id for requirements in _REQUIREMENTS.values() for check_id in requirements
)
TERMINOLOGY_RELEASE_EVIDENCE_SCHEMA_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class TerminologyReleaseEvidenceLoadResult:
    evidence: TerminologyReleaseEvidence
    diagnostics: tuple[str, ...] = ()


class TerminologyReleaseEvidenceLoader:
    """Load a digest-bound release-evidence file without ever failing open."""

    def load(self, path: str | Path | None) -> TerminologyReleaseEvidenceLoadResult:
        if path is None or not str(path).strip():
            return TerminologyReleaseEvidenceLoadResult(
                TerminologyReleaseEvidence(),
                ("release-evidence-path-not-configured",),
            )
        target = Path(path)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            evidence = self._decode(payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return TerminologyReleaseEvidenceLoadResult(
                TerminologyReleaseEvidence(),
                (f"release-evidence-invalid:{type(exc).__name__}",),
            )
        return TerminologyReleaseEvidenceLoadResult(evidence)

    @staticmethod
    def _decode(value: Any) -> TerminologyReleaseEvidence:
        if not isinstance(value, dict):
            raise ValueError("release evidence must be a JSON object")
        if value.get("schema_version") != TERMINOLOGY_RELEASE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported terminology release-evidence schema")
        digest = value.get("artifact_digest")
        unsigned = {key: item for key, item in value.items() if key != "artifact_digest"}
        if not isinstance(digest, str) or digest != _release_evidence_digest(unsigned):
            raise ValueError("terminology release-evidence digest mismatch")
        checks = value.get("checks")
        if not isinstance(checks, list):
            raise ValueError("terminology release evidence checks must be a list")
        decoded: list[GateCheck] = []
        for item in checks:
            if not isinstance(item, dict) or set(item) - {"check_id", "status", "detail"}:
                raise ValueError("invalid terminology release-evidence check record")
            check_id = item.get("check_id")
            if check_id not in TERMINOLOGY_GATE_CHECK_IDS:
                raise ValueError(f"unknown terminology release-evidence check: {check_id}")
            decoded.append(
                GateCheck(
                    str(check_id),
                    GateCheckStatus(item.get("status")),
                    str(item.get("detail", "")),
                )
            )
        return TerminologyReleaseEvidence(tuple(decoded))


def _release_evidence_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TerminologyFeatureGateEvaluator:
    """Project ordered release-readiness stages from immutable validation evidence.

    Missing checks are ``not-run`` and therefore block the stage.  A later
    stage can never be enabled while an earlier stage is disabled.  These
    results are release evidence and must not control installed-app behavior.
    """

    def evaluate(self, evidence: TerminologyReleaseEvidence | None = None) -> tuple[TerminologyStageGate, ...]:
        evidence = evidence or TerminologyReleaseEvidence()
        gates: list[TerminologyStageGate] = []
        previous_enabled = True
        for stage in TERMINOLOGY_FEATURE_STAGE_ORDER:
            blockers = [
                f"{check_id}:{evidence.status(check_id).value}"
                for check_id in _REQUIREMENTS[stage]
                if evidence.status(check_id) is not GateCheckStatus.PASSED
            ]
            blockers.extend(_STAGE_POLICY_BLOCKERS.get(stage, ()))
            if not previous_enabled:
                blockers.insert(0, f"previous-stage-disabled:{TERMINOLOGY_FEATURE_STAGE_ORDER[len(gates) - 1].value}")
            enabled = previous_enabled and not blockers
            gates.append(TerminologyStageGate(stage, enabled, tuple(blockers)))
            previous_enabled = enabled
        return tuple(gates)

    def capabilities(self, evidence: TerminologyReleaseEvidence | None = None) -> Mapping[str, bool]:
        gates = self.evaluate(evidence)
        return MappingProxyType({gate.stage.value: gate.enabled for gate in gates})


__all__ = [
    "GateCheck",
    "GateCheckStatus",
    "TERMINOLOGY_FEATURE_STAGE_ORDER",
    "TERMINOLOGY_GATE_CHECK_IDS",
    "TERMINOLOGY_RELEASE_EVIDENCE_SCHEMA_VERSION",
    "TerminologyFeatureGateEvaluator",
    "TerminologyFeatureStage",
    "TerminologyReleaseEvidence",
    "TerminologyReleaseEvidenceLoader",
    "TerminologyReleaseEvidenceLoadResult",
    "TerminologyStageGate",
]
