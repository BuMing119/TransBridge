# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Describe incremental saves without claiming missing source content was captured."""

from typing import Any

from transbridge.application.contracts import (
    Diagnostic,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
)

from .models import ActiveProject, LifecycleSave

MAX_SAVE_ENTRY_DELTA_KEYS = 100_000


def translations_were_saved(result):
    """Recognize only the explicit source-incomplete save receipt for navigation."""
    return (
        getattr(result, "outcome", None) is OperationOutcome.PARTIAL
        and isinstance(result.value, dict)
        and result.value.get("dirty") is False
        and result.value.get("source_content_complete") is False
        and any(item.code == "PROJECT_SAVE_SOURCE_CONTENT_MISSING" for item in result.diagnostics)
    )


def active_save_result(
    active: ActiveProject | None,
    context: RequestContext,
    *,
    save: LifecycleSave | None = None,
    include_entry_delta: bool = False,
) -> OperationResult[dict[str, Any] | None]:
    if active is None:
        return OperationResult.completed(None, run_id=context.run_id)
    missing_inputs = [item.to_dict() for item in active.missing_inputs]
    known = {item.source_id for item in active.missing_inputs}
    # Empty Variants still belong to a Project's registered source set. Inspect
    # its explicit capture state only; saving never reloads or hashes artifacts.
    missing_inputs.extend(
        {"source_id": source["source_id"], "display_name": source.get("display_name"), "content_state": "missing"}
        for source in active.project.envelope.data.get("sources", ())
        if source["content_state"] == "missing" and source["source_id"] not in known
    )
    if not missing_inputs:
        summary = active.summary()
        if include_entry_delta:
            delta = _entry_delta(active, save)
            if delta is not None:
                summary["entry_delta"] = delta
        return OperationResult.completed(summary, run_id=context.run_id)
    summary = {**active.summary(), "source_content_complete": False}
    return OperationResult.partial(
        summary,
        counts=OperationCounts(succeeded=1, failed=len(missing_inputs)),
        diagnostics=(
            Diagnostic(
                "PROJECT_SAVE_SOURCE_CONTENT_MISSING",
                "已有译文和修改已保留；工程仍缺少来源内容，尚未完成自包含保存，请补充输入。",
                category=ErrorCategory.PREREQUISITE,
                details=(("missing_inputs", missing_inputs),),
            ),
        ),
        run_id=context.run_id,
    )


def _entry_delta(active: ActiveProject, save: LifecycleSave | None) -> dict[str, Any] | None:
    """Report only row changes covered by the successfully persisted revision range."""
    ref = active.formal_variant_ref
    target_revision = active.persisted_variant_revision
    if ref is None or target_revision is None:
        return None

    if save is None:
        previous_revision, entries = target_revision, ()
    else:
        previous_revision, entries = save.expected_persisted_revision, save.entry_changes
        if (
            save.variant is None
            or save.variant.ref != ref
            or save.variant.revision != target_revision
            or save.project.envelope.revision != save.expected_persisted_project_revision
            or active.persisted_project_revision != save.expected_persisted_project_revision
            or save.source_additions
        ):
            return None
    if (
        previous_revision is None
        or previous_revision > target_revision
        or entries is None
        or len(entries) > MAX_SAVE_ENTRY_DELTA_KEYS
    ):
        return None

    return {
        "project_id": ref.project_id.value,
        "variant_id": ref.identity.value,
        "previous_variant_revision": previous_revision,
        "variant_revision": target_revision,
        "entry_keys": [entry.entry_key.to_dict() for entry in entries],
    }
