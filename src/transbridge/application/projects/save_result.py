"""Describe incremental saves without claiming missing source content was captured."""

from transbridge.application.contracts import (
    Diagnostic,
    ErrorCategory,
    OperationCounts,
    OperationOutcome,
    OperationResult,
)


def translations_were_saved(result):
    """Recognize only the explicit source-incomplete save receipt for navigation."""
    return (
        getattr(result, "outcome", None) is OperationOutcome.PARTIAL
        and isinstance(result.value, dict)
        and result.value.get("dirty") is False
        and result.value.get("source_content_complete") is False
        and any(item.code == "PROJECT_SAVE_SOURCE_CONTENT_MISSING" for item in result.diagnostics)
    )


def active_save_result(active, context):
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
        return OperationResult.completed(active.summary(), run_id=context.run_id)
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
