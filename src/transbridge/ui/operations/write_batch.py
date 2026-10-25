"""Batch write planning and per-source publication results."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path

from transbridge.application.contracts import (
    Diagnostic,
    DiagnosticSeverity,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)
from transbridge.application.io.operation_write import (
    HydratedWriteDraft,
    HydratedWritePreflight,
    HydratedWriteWorkload,
    WritePreflightCheck,
)
from transbridge.application.io.publish import ConflictPolicy as PublishConflictPolicy

from .mappers import OperationPlanDraft
from .plan_view import EditableControl, EditableFieldState


def batch_output_conflicts(preflights) -> tuple[str, ...]:
    """Detect cross-source aliases for every primary and companion output."""
    return tuple(
        f"{' 与'.join(f'来源 {index}' for index in owners)} 的输出路径重叠：{path}"
        for path, owners in _conflicting_outputs(preflights).items()
    )


def isolate_batch_output_conflicts(preflights) -> tuple:
    conflicts = _conflicting_outputs(preflights)
    output = []
    for index, checked in enumerate(preflights, 1):
        paths = tuple(path for path, owners in conflicts.items() if index in owners)
        if paths and not isinstance(checked, _WriteBatchFailure):
            checked = replace(
                checked,
                checks=(
                    *checked.checks,
                    WritePreflightCheck("BATCH_OUTPUT_CONFLICT", False, "输出与其他来源重叠：" + "；".join(paths)),
                ),
            )
        output.append(checked)
    return tuple(output)


def _conflicting_outputs(preflights) -> dict[str, list[int]]:
    owners: dict[str, list[int]] = {}
    for index, checked in enumerate(preflights, 1):
        paths = (
            (checked.target_path,)
            if isinstance(checked, _WriteBatchFailure)
            else (path for path, _fingerprint in checked.artifact_fingerprints)
        )
        for path in paths:
            try:
                canonical = os.path.normcase(str(Path(path).resolve()))
            except (OSError, ValueError):
                continue  # This source already has a failed preflight and cannot write.
            items = owners.setdefault(canonical, [])
            if index not in items:
                items.append(index)
    return {path: items for path, items in owners.items() if len(items) > 1}


@dataclass(frozen=True, slots=True)
class _WriteBatchFailure:
    target_path: str
    label: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class _HydratedWriteBatch:
    drafts: tuple[HydratedWriteDraft, ...]
    failures: tuple[_WriteBatchFailure, ...] = ()

    def __post_init__(self) -> None:
        if len(self.drafts) + len(self.failures) < 2:
            raise ValueError("批量写回至少需要两个来源文件")


class _HydratedWriteBatchWorkload:
    supports_cancel = True

    def __init__(self, preflights) -> None:
        self._preflights = tuple(preflights)

    def __call__(self, run_context):
        outcomes = []
        diagnostics = []
        artifacts = []
        succeeded = failed = cancelled = partial = 0
        for checked in self._preflights:
            draft = checked if isinstance(checked, _WriteBatchFailure) else checked.draft
            if bool(getattr(run_context.cancellation, "is_cancelled", False)):
                cancelled += 1
                outcomes.append(_write_object_outcome(draft, "cancelled", "WRITE_CANCELLED"))
                continue
            result = _run_batch_member(checked, run_context)
            if result.outcome is OperationOutcome.COMPLETED:
                succeeded += 1
                status = "succeeded"
            elif result.outcome is OperationOutcome.CANCELLED:
                cancelled += 1
                status = "cancelled"
            else:
                failed += 1
                status = "failed"
                if result.outcome is OperationOutcome.PARTIAL:
                    partial += 1
            diagnostics.extend(result.diagnostics)
            item = _write_object_outcome(
                draft,
                status,
                result.diagnostics[0].code if result.diagnostics else result.outcome.value,
            )
            item["outcome"] = result.outcome.value
            item["artifact_refs"] = tuple(result.artifact_refs)
            outcomes.append(item)
            artifacts.extend(result.artifact_refs)
        value = {"outcomes": tuple(outcomes)}
        # A partial member has committed output and a remaining failure. Count
        # both so the batch retains those side effects without calling it safe.
        counts = OperationCounts(succeeded=succeeded + partial, failed=failed, cancelled=cancelled)
        if failed == 0 and cancelled == 0:
            return OperationResult.completed(
                value,
                counts=counts,
                diagnostics=tuple(diagnostics),
                artifact_refs=tuple(artifacts),
                run_id=run_context.ref.run_id,
            )
        if succeeded or partial:
            if not any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics):
                diagnostics.append(
                    Diagnostic(
                        "WRITE_BATCH_INCOMPLETE",
                        "批量写回未完成全部来源。",
                        severity=DiagnosticSeverity.ERROR,
                        category=ErrorCategory.CANCELLED if cancelled else ErrorCategory.EXTERNAL,
                    )
                )
            return OperationResult.partial(
                value,
                counts=counts,
                diagnostics=tuple(diagnostics),
                artifact_refs=tuple(artifacts),
                run_id=run_context.ref.run_id,
            )
        # Failed/cancelled OperationResult values cannot carry committed payloads.
        # Keep per-source receipts once, in structured diagnostic evidence.
        diagnostics.append(
            Diagnostic(
                "WRITE_BATCH_RESULTS",
                "批量写回已取消。" if not failed else "批量写回失败，未完成任何来源。",
                severity=DiagnosticSeverity.WARNING if not failed else DiagnosticSeverity.ERROR,
                category=ErrorCategory.CANCELLED if not failed else ErrorCategory.EXTERNAL,
                details=(("outcomes", tuple(outcomes)),),
            )
        )
        return OperationResult(
            OperationOutcome.CANCELLED if not failed else OperationOutcome.FAILED,
            counts=counts,
            diagnostics=tuple(diagnostics),
            run_id=run_context.ref.run_id,
        )


def _write_batch_plan(request: _HydratedWriteBatch) -> OperationPlanDraft:
    targets = tuple(item.target_path for item in (*request.drafts, *request.failures))
    existing = tuple(path for path in targets if Path(path).exists())
    entries = tuple(entry for item in request.drafts for entry in item.entries)
    overwrite = all(item.conflict_policy is PublishConflictPolicy.EXPLICIT_OVERWRITE for item in request.drafts)
    fingerprint = hashlib.sha256(
        "\0".join([
            *(f"{item.source_snapshot.sha256}:{item.variant_revision}" for item in request.drafts),
            *(f"{item.target_path}:{item.code}:{item.message}" for item in request.failures),
        ]).encode()
    ).hexdigest()
    return OperationPlanDraft(
        request=request,
        target="；".join(targets),
        target_revision=f"{len(existing)} existing / {len(targets) - len(existing)} missing",
        input_fingerprint=fingerprint,
        scope_summary=f"{len(targets)} 个来源文件，{len(entries)} 个条目",
        mode_summary="分别验证并写出各来源文件",
        conflict_summary="全部覆盖已确认" if overwrite else "保留已有输出，继续写出其他文件",
        backup_summary="覆盖输出文件前创建备份",
        is_batch=True,
        estimated_impact=(("objects", len(entries)), ("files", len(targets))),
        editable_fields=(
            EditableFieldState(
                "overwrite_confirmed",
                "覆盖确认",
                "true" if overwrite else "false",
                control=EditableControl.BOOLEAN,
            ),
        ),
        locked_count=sum(getattr(item, "stage", 0) == 9 for item in entries),
        hidden_count=sum(getattr(item, "stage", 0) == -1 for item in entries),
        # Coverage is checked per source by the domain preflight and publisher.
        # An unrelated existing target must not block the whole batch.
        overwrite_risk=False,
        overwrite_confirmed=overwrite,
        backup_required=bool(existing),
        backup_enabled=True,
        expected_side_effects=("分别验证并写出各来源，保留未完成来源和已完成结果",),
    )


def _write_object_outcome(draft: HydratedWriteDraft | _WriteBatchFailure, status: str, code: str) -> dict[str, object]:
    return {
        "object_ref": draft.target_path,
        "label": draft.label if isinstance(draft, _WriteBatchFailure) else Path(draft.target_path).name,
        "status": status,
        "code": code,
        "retryable": False,
    }


def _run_batch_member(checked: HydratedWritePreflight | _WriteBatchFailure, run_context) -> OperationResult:
    if isinstance(checked, _WriteBatchFailure):
        return OperationResult.failed(
            DomainError(ErrorCategory.EXTERNAL, checked.code, checked.message), run_id=run_context.ref.run_id
        )
    if not checked.ready:
        blocked = tuple(item for item in checked.checks if not item.passed and not item.warning)
        return OperationResult(
            OperationOutcome.FAILED,
            diagnostics=tuple(Diagnostic(item.code, item.message) for item in blocked),
            counts=OperationCounts(failed=1),
            run_id=run_context.ref.run_id,
        )
    try:
        return HydratedWriteWorkload(checked)(run_context)
    except (OSError, TypeError, ValueError) as exc:
        return OperationResult.failed(
            DomainError(
                ErrorCategory.EXTERNAL, "WRITE_SOURCE_FAILED", f"{Path(checked.draft.target_path).name}：{exc}"
            ),
            run_id=run_context.ref.run_id,
        )
