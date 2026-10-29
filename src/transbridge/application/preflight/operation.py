"""Qt-free operation preflight contracts and existing shared policy checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from transbridge.application.security.hitl import ConfirmationToken


class OperationKind(StrEnum):
    UPLOAD = "upload"
    DOWNLOAD = "download"
    WRITE = "write"
    FOMOD = "fomod"


class PreflightCheckStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class PreflightCheckState:
    check_id: str
    label: str
    status: PreflightCheckStatus
    reason: str = ""
    repair_intent: str | None = None

    def __post_init__(self) -> None:
        if not self.check_id.strip() or not self.label.strip():
            raise ValueError("preflight checks require an id and label")
        if self.status is not PreflightCheckStatus.PASSED and not self.reason.strip():
            raise ValueError("non-passing preflight checks require a reason")


@dataclass(frozen=True, slots=True)
class OperationPreflightResult:
    kind: OperationKind
    request_digest: str
    target_revision: str
    checks: tuple[PreflightCheckState, ...]
    expected_side_effects: tuple[str, ...]
    confirmation_token: ConfirmationToken | None = None
    estimated_impact: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if len(self.request_digest) != 64 or not self.target_revision.strip():
            raise ValueError("preflight requires a request digest and target revision")
        if len({item.check_id for item in self.checks}) != len(self.checks):
            raise ValueError("preflight check ids must be unique")
        if len({key for key, _ in self.estimated_impact}) != len(self.estimated_impact):
            raise ValueError("preflight impact keys must be unique")
        if any(value < 0 for _, value in self.estimated_impact):
            raise ValueError("preflight impact values must not be negative")

    @property
    def ready(self) -> bool:
        return not any(item.status is PreflightCheckStatus.BLOCKED for item in self.checks)


class DomainPreflightPort(Protocol):
    def __call__(self, request: object) -> DomainPreflightResult | tuple[PreflightCheckState, ...]: ...


@dataclass(frozen=True, slots=True)
class DomainPreflightResult:
    checks: tuple[PreflightCheckState, ...]
    estimated_impact: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class OperationPreflightFacts:
    """Frozen feature facts; adapters provide evidence without owning policy."""

    request_digest: str
    target_revision: str
    estimated_impact: tuple[tuple[str, int], ...] = ()
    expected_side_effects: tuple[str, ...] = ()
    input_ready: bool = True
    output_ready: bool = True
    locked_count: int = 0
    hidden_count: int = 0
    overwrite_risk: bool = False
    overwrite_confirmed: bool = False
    backup_required: bool = False
    backup_enabled: bool = False


class OperationPreflightService:
    """Evaluate existing input/output, publish and stage policies."""

    def evaluate(
        self,
        kind: OperationKind,
        facts: OperationPreflightFacts,
        domain: DomainPreflightResult | tuple[PreflightCheckState, ...] = (),
    ) -> OperationPreflightResult:
        if isinstance(domain, DomainPreflightResult):
            domain_checks = domain.checks
            estimated_impact = domain.estimated_impact or facts.estimated_impact
        else:
            domain_checks = tuple(domain)
            estimated_impact = facts.estimated_impact
        specific = (_stage_check(facts),) if kind is OperationKind.WRITE else ()
        return OperationPreflightResult(
            kind,
            facts.request_digest,
            facts.target_revision,
            (*self._shared_checks(facts), *specific, *domain_checks),
            facts.expected_side_effects,
            estimated_impact=estimated_impact,
        )

    @staticmethod
    def _shared_checks(value: OperationPreflightFacts) -> tuple[PreflightCheckState, ...]:
        return (
            _check("INPUT", "输入有效", value.input_ready, "输入不存在、已变化或不完整"),
            _check("OUTPUT", "输出可用", value.output_ready, "输出路径不可写或不支持安全提交"),
            _check(
                "OVERWRITE",
                "覆盖策略",
                not value.overwrite_risk or value.overwrite_confirmed,
                "目标已存在，必须显式确认覆盖",
            ),
            _check(
                "BACKUP",
                "备份策略",
                not value.backup_required or value.backup_enabled,
                "该覆盖操作要求先创建备份",
            ),
        )


def _check(
    check_id: str,
    label: str,
    passed: bool,
    reason: str,
    repair_intent: str | None = None,
) -> PreflightCheckState:
    return PreflightCheckState(
        check_id,
        label,
        PreflightCheckStatus.PASSED if passed else PreflightCheckStatus.BLOCKED,
        "" if passed else reason,
        repair_intent,
    )


def _stage_check(value: OperationPreflightFacts) -> PreflightCheckState:
    detail = f"锁定 {value.locked_count}，隐藏 {value.hidden_count}"
    return PreflightCheckState(
        "LOCKED_HIDDEN",
        "锁定/隐藏条目策略",
        PreflightCheckStatus.WARNING if (value.locked_count or value.hidden_count) else PreflightCheckStatus.PASSED,
        detail if (value.locked_count or value.hidden_count) else "",
    )
