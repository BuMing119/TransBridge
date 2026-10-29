"""Small per-domain mappers for the shared operation-plan presentation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from transbridge.application.preflight.operation import (
    DomainPreflightPort as DomainPreflightPort,
    DomainPreflightResult as DomainPreflightResult,
    OperationPreflightFacts,
    OperationPreflightResult,
    OperationPreflightService,
)

from .plan_view import EditableFieldState, OperationKind, OperationPlanViewState


@dataclass(frozen=True, slots=True)
class OperationPlanDraft:
    """Presentation envelope; ``request`` stays owned by its feature use case."""

    request: object
    target: str
    target_revision: str
    input_fingerprint: str
    scope_summary: str
    mode_summary: str
    conflict_summary: str
    backup_summary: str
    estimated_impact: tuple[tuple[str, int], ...]
    editable_fields: tuple[EditableFieldState, ...] = ()
    warnings: tuple[str, ...] = ()
    input_ready: bool = True
    output_ready: bool = True
    locked_count: int = 0
    hidden_count: int = 0
    overwrite_risk: bool = False
    overwrite_confirmed: bool = False
    backup_required: bool = False
    backup_enabled: bool = False
    expected_side_effects: tuple[str, ...] = ()
    is_batch: bool = False

    def __post_init__(self) -> None:
        if not self.target.strip() or not self.target_revision.strip() or not self.input_fingerprint.strip():
            raise ValueError("operation draft target, revision, and input fingerprint are required")
        if min(self.locked_count, self.hidden_count) < 0:
            raise ValueError("locked/hidden counts must not be negative")

    @property
    def request_digest(self) -> str:
        payload = {
            "target": self.target,
            "target_revision": self.target_revision,
            "input_fingerprint": self.input_fingerprint,
            "scope": self.scope_summary,
            "mode": self.mode_summary,
            "conflict": self.conflict_summary,
            "backup": self.backup_summary,
            "estimated": self.estimated_impact,
            "editable": tuple((item.field_id, item.value) for item in self.editable_fields),
            "locked": self.locked_count,
            "hidden": self.hidden_count,
            "overwrite": (self.overwrite_risk, self.overwrite_confirmed),
            "backup_policy": (self.backup_required, self.backup_enabled),
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(data).hexdigest()


class _OperationMapper:
    kind: OperationKind
    title: str

    def __init__(self, domain_preflight: DomainPreflightPort | None = None) -> None:
        self._domain_preflight = domain_preflight or (lambda _request: ())

    def present(self, session_id: str, revision: int, draft: object) -> OperationPlanViewState:
        value = self._draft(draft)
        return OperationPlanViewState(
            session_id=session_id,
            revision=revision,
            kind=self.kind,
            title=self.title,
            target=value.target,
            scope_summary=value.scope_summary,
            mode_summary=value.mode_summary,
            conflict_summary=value.conflict_summary,
            backup_summary=value.backup_summary,
            estimated_impact=value.estimated_impact,
            editable_fields=value.editable_fields,
            warnings=value.warnings,
            request_digest=value.request_digest,
            is_batch=value.is_batch,
        )

    def preflight(self, draft: object) -> OperationPreflightResult:
        value = self._draft(draft)
        facts = OperationPreflightFacts(
            request_digest=value.request_digest,
            target_revision=value.target_revision,
            estimated_impact=value.estimated_impact,
            expected_side_effects=value.expected_side_effects,
            input_ready=value.input_ready,
            output_ready=value.output_ready,
            locked_count=value.locked_count,
            hidden_count=value.hidden_count,
            overwrite_risk=value.overwrite_risk,
            overwrite_confirmed=value.overwrite_confirmed,
            backup_required=value.backup_required,
            backup_enabled=value.backup_enabled,
        )
        return OperationPreflightService().evaluate(self.kind, facts, self._domain_preflight(value.request))

    @staticmethod
    def _draft(value: object) -> OperationPlanDraft:
        if not isinstance(value, OperationPlanDraft):
            raise TypeError("operation mapper requires an OperationPlanDraft")
        return value


class WriteOperationMapper(_OperationMapper):
    kind = OperationKind.WRITE
    title = "写回计划"


class FomodOperationMapper(_OperationMapper):
    kind = OperationKind.FOMOD
    title = "FOMOD 构建计划"
