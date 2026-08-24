"""Small per-domain mappers for the shared operation-plan presentation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Protocol

from .plan_view import EditableFieldState, OperationKind, OperationPlanViewState
from .preflight_view import (
    OperationPreflightResult,
    PreflightCheckState,
    PreflightCheckStatus,
)


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
    credentials_ready: bool = True
    permission_ready: bool = True
    input_ready: bool = True
    output_ready: bool = True
    locked_count: int = 0
    hidden_count: int = 0
    overwrite_risk: bool = False
    overwrite_confirmed: bool = False
    backup_required: bool = False
    backup_enabled: bool = False
    dirty_target: bool = False
    expected_side_effects: tuple[str, ...] = ()

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
            "dirty_target": self.dirty_target,
        }
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(data).hexdigest()


class DomainPreflightPort(Protocol):
    def __call__(self, request: object) -> tuple[PreflightCheckState, ...]: ...


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
        )

    def preflight(self, draft: object) -> OperationPreflightResult:
        value = self._draft(draft)
        checks = (*self._shared_checks(value), *self._specific_checks(value), *self._domain_preflight(value.request))
        return OperationPreflightResult(
            self.kind,
            value.request_digest,
            value.target_revision,
            tuple(checks),
            value.expected_side_effects,
        )

    @staticmethod
    def _draft(value: object) -> OperationPlanDraft:
        if not isinstance(value, OperationPlanDraft):
            raise TypeError("operation mapper requires an OperationPlanDraft")
        return value

    @staticmethod
    def _shared_checks(value: OperationPlanDraft) -> tuple[PreflightCheckState, ...]:
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

    def _specific_checks(self, value: OperationPlanDraft) -> tuple[PreflightCheckState, ...]:
        del value
        return ()


class UploadOperationMapper(_OperationMapper):
    kind = OperationKind.UPLOAD
    title = "上传计划"

    def _specific_checks(self, value: OperationPlanDraft) -> tuple[PreflightCheckState, ...]:
        return (
            _check("CREDENTIAL", "ParaTranz 凭据", value.credentials_ready, "未配置有效凭据", "settings.paratranz"),
            _check("PERMISSION", "远端写入权限", value.permission_ready, "当前账号无上传权限"),
            _stage_check(value),
        )


class DownloadOperationMapper(_OperationMapper):
    kind = OperationKind.DOWNLOAD
    title = "下载并合并计划"

    def _specific_checks(self, value: OperationPlanDraft) -> tuple[PreflightCheckState, ...]:
        return (
            _check("CREDENTIAL", "ParaTranz 凭据", value.credentials_ready, "未配置有效凭据", "settings.paratranz"),
            _check("PERMISSION", "远端读取权限", value.permission_ready, "当前账号无下载权限"),
            _check("DIRTY_VARIANT", "本地版本状态", not value.dirty_target, "当前版本有未保存修改，不能直接合并"),
        )


class WriteOperationMapper(_OperationMapper):
    kind = OperationKind.WRITE
    title = "写回计划"

    def _specific_checks(self, value: OperationPlanDraft) -> tuple[PreflightCheckState, ...]:
        return (_stage_check(value),)


class FomodOperationMapper(_OperationMapper):
    kind = OperationKind.FOMOD
    title = "FOMOD 构建计划"


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


def _stage_check(value: OperationPlanDraft) -> PreflightCheckState:
    detail = f"锁定 {value.locked_count}，隐藏 {value.hidden_count}"
    return PreflightCheckState(
        "LOCKED_HIDDEN",
        "锁定/隐藏条目策略",
        PreflightCheckStatus.WARNING if (value.locked_count or value.hidden_count) else PreflightCheckStatus.PASSED,
        detail if (value.locked_count or value.hidden_count) else "",
    )
