"""Shared per-source batch write execution and output-conflict checks."""

from __future__ import annotations

from dataclasses import dataclass, replace
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

from .operation_write import HydratedWriteDraft, HydratedWritePreflight, HydratedWriteWorkload, WritePreflightCheck


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
        if paths and not isinstance(checked, WriteBatchFailure):
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
            if isinstance(checked, WriteBatchFailure)
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
class WriteBatchFailure:
    target_path: str
    label: str
    code: str
    message: str
    source_path: str = ""


class HydratedWriteBatchWorkload:
    supports_cancel = True

    def __init__(self, preflights) -> None:
        self._preflights = tuple(preflights)

    def __call__(self, run_context):
        outcomes = []
        diagnostics = []
        artifacts = []
        succeeded = failed = cancelled = partial = 0
        for checked in self._preflights:
            draft = checked if isinstance(checked, WriteBatchFailure) else checked.draft
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


def _write_object_outcome(draft: HydratedWriteDraft | WriteBatchFailure, status: str, code: str) -> dict[str, object]:
    return {
        "object_ref": draft.target_path,
        "label": draft.label if isinstance(draft, WriteBatchFailure) else Path(draft.target_path).name,
        "status": status,
        "code": code,
        "retryable": False,
    }


def _run_batch_member(checked: HydratedWritePreflight | WriteBatchFailure, run_context) -> OperationResult:
    if isinstance(checked, WriteBatchFailure):
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
