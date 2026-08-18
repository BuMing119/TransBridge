"""Contract tests for application operation and capability DTOs."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json

import pytest

from transbridge.application.capabilities import (
    CapabilityId,
    CapabilityRegistry,
    CapabilityReport,
    CapabilityState,
    capability_report_json_schema,
)
from transbridge.application.contracts import (
    Deferred,
    Diagnostic,
    DomainError,
    ErrorCategory,
    JobRef,
    OperationCounts,
    OperationOutcome,
    OperationResult,
    RequestContext,
    map_exception,
    operation_result_from_tool_result,
    operation_result_json_schema,
)
from transbridge.smart_assistant.tools.types import ToolResult


def test_completed_result_round_trips_through_json() -> None:
    result = OperationResult.completed(
        {"translated": 3},
        counts=OperationCounts(succeeded=3),
        artifact_refs=("artifact:report",),
        run_id="run-1",
    )

    payload = json.loads(json.dumps(result.to_dict()))

    assert OperationResult.from_dict(payload) == result
    assert result.outcome is OperationOutcome.COMPLETED
    assert result.is_success is True


def test_terminal_outcome_is_one_enum_value_and_instance_is_frozen() -> None:
    result = OperationResult.completed("done")

    with pytest.raises(FrozenInstanceError):
        result.outcome = OperationOutcome.FAILED  # type: ignore[misc]


@pytest.mark.parametrize(
    ("outcome", "kwargs"),
    [
        (
            OperationOutcome.COMPLETED,
            {"counts": OperationCounts(failed=1)},
        ),
        (
            OperationOutcome.COMPLETED,
            {"counts": OperationCounts(cancelled=1)},
        ),
        (
            OperationOutcome.PARTIAL,
            {"counts": OperationCounts(succeeded=1, failed=1)},
        ),
        (OperationOutcome.FAILED, {"value": "must-not-commit"}),
        (OperationOutcome.CANCELLED, {"artifact_refs": ("artifact:x",)}),
    ],
)
def test_invalid_outcome_combinations_are_rejected(outcome: OperationOutcome, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        OperationResult(outcome, **kwargs)


def test_partial_requires_success_failure_counts_and_diagnostic() -> None:
    diagnostic = Diagnostic("ITEM_FAILED", "One item failed.")
    result = OperationResult.partial(
        {"items": ["ok"]},
        counts=OperationCounts(succeeded=1, failed=1),
        diagnostics=(diagnostic,),
    )

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.counts.total == 2
    assert result.diagnostics == (diagnostic,)


def test_exception_mapping_never_produces_completed() -> None:
    exceptions: list[BaseException] = [
        ValueError("bad input"),
        FileNotFoundError("C:/secret/project.json"),
        PermissionError("token=secret"),
        TimeoutError("upstream timeout"),
        RuntimeError("password=hunter2 at C:/private/file"),
        asyncio.CancelledError(),
    ]

    for exception in exceptions:
        result = OperationResult.from_exception(exception)
        assert result.outcome is not OperationOutcome.COMPLETED


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (ValueError("bad"), ErrorCategory.INPUT),
        (FileNotFoundError("missing"), ErrorCategory.PREREQUISITE),
        (PermissionError("denied"), ErrorCategory.PERMISSION),
        (ConnectionError("offline"), ErrorCategory.EXTERNAL),
        (asyncio.CancelledError(), ErrorCategory.CANCELLED),
        (RuntimeError("boom"), ErrorCategory.INTERNAL),
    ],
)
def test_exception_categories_are_stable(exception: BaseException, category: ErrorCategory) -> None:
    assert map_exception(exception).category is category


def test_unexpected_exception_serialization_redacts_cause() -> None:
    secret = "api_token=top-secret C:/private/customer.json"
    result = OperationResult.from_exception(RuntimeError(secret))
    serialized = json.dumps(result.to_dict())

    assert result.outcome is OperationOutcome.FAILED
    assert secret not in serialized
    assert "top-secret" not in serialized
    assert result.diagnostics[0].code == "INTERNAL_ERROR"


def test_existing_domain_error_retains_safe_details_and_cause() -> None:
    cause = RuntimeError("trusted-log-only")
    error = DomainError(
        ErrorCategory.CONFLICT,
        "REVISION_CONFLICT",
        "The resource changed.",
        details={"expected_revision": 3},
        cause=cause,
    )

    mapped = map_exception(error)

    assert mapped is error
    assert mapped.__cause__ is cause
    assert "trusted-log-only" not in json.dumps(mapped.to_dict())


def test_cancelled_result_does_not_carry_committable_value() -> None:
    result = OperationResult.from_exception(asyncio.CancelledError(), run_id="run-cancel")

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.value is None
    assert result.artifact_refs == ()
    assert result.counts.cancelled == 1


def test_non_json_value_is_rejected_at_serialization_boundary() -> None:
    result = OperationResult.completed(object())

    with pytest.raises(TypeError, match="non-JSON-serializable"):
        result.to_dict()


def test_serialized_count_total_cannot_disagree_with_components() -> None:
    payload = OperationResult.completed(counts=OperationCounts(succeeded=2)).to_dict()
    payload["counts"]["total"] = 99

    with pytest.raises(ValueError, match="total"):
        OperationResult.from_dict(payload)


def test_request_context_round_trip_is_stable_and_backward_compatible() -> None:
    context = RequestContext(
        owner_id="gui:window-1",
        run_id="run-2",
        project_id="project-1",
        permissions=frozenset({"read", "write"}),
        authorized_roots=("D:/Projects",),
        metadata=(("entrypoint", "gui"),),
    )

    payload = context.to_dict()
    payload.pop("schema_version")

    assert RequestContext.from_dict(payload) == context


def test_job_ref_and_deferred_have_explicit_serializable_shape() -> None:
    deferred = Deferred(JobRef("unpredictable-job-id", "owner-1", "run-3"))

    payload = deferred.to_dict()
    restored = Deferred.job_from_dict(json.loads(json.dumps(payload)))

    assert payload["kind"] == "deferred"
    assert restored == deferred


def test_legacy_tool_result_partial_mapping_is_lossless() -> None:
    tool_result = ToolResult.partial_ok(
        "Two items succeeded and one failed.",
        data={"succeeded": ["a", "b"]},
        failed_items=[{"id": "c", "reason": "conflict"}],
    )
    tool_result.warnings = ["Review the failed item."]

    result = operation_result_from_tool_result(tool_result, succeeded_count=2)

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.value is not None
    assert result.value["presentation_status"] == "[PARTIAL]"
    assert result.value["failed_items"] == tool_result.failed_items
    assert result.counts == OperationCounts(succeeded=2, failed=1)
    assert {diagnostic.code for diagnostic in result.diagnostics} == {
        "LEGACY_TOOL_WARNING",
        "LEGACY_PARTIAL_RESULT",
    }
    assert OperationResult.from_dict(result.to_dict()) == result


def test_legacy_tool_failure_keeps_structured_fields_in_diagnostic() -> None:
    tool_result = ToolResult.fail(
        "Revision changed.",
        failed_items=[{"id": "entry-1"}],
        error_category="conflict",
        error_code="REVISION_CONFLICT",
        recovery_action="reload",
    )

    result = operation_result_from_tool_result(tool_result)

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].category is ErrorCategory.CONFLICT
    details = dict(result.diagnostics[0].details)
    assert details["legacy_result"]["failed_items"] == [{"id": "entry-1"}]
    assert details["legacy_result"]["recovery_action"] == "reload"


def test_legacy_cancelled_failure_maps_to_cancelled_not_failed() -> None:
    tool_result = ToolResult.fail(
        "Cancelled at a safe point.",
        error_category="cancelled",
        error_code="OPERATION_CANCELLED",
    )

    result = operation_result_from_tool_result(tool_result)

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.counts.cancelled == 1


def test_legacy_internal_failure_does_not_expose_raw_message_or_data() -> None:
    tool_result = ToolResult(
        success=False,
        message="token=top-secret at C:/private/file",
        data={"api_key": "top-secret"},
        error_category="internal",
    )

    serialized = json.dumps(operation_result_from_tool_result(tool_result).to_dict())

    assert "top-secret" not in serialized
    assert "C:/private" not in serialized


def test_registered_capability_reports_all_three_states() -> None:
    reports = (
        CapabilityReport(CapabilityId("format.esp.read"), CapabilityState.AVAILABLE),
        CapabilityReport(
            CapabilityId("archive.rar.write"),
            CapabilityState.DEGRADED,
            reasons=("Solid archives are not supported.",),
        ),
        CapabilityReport(
            CapabilityId("format.sst.write"),
            CapabilityState.UNAVAILABLE,
            missing_prerequisites=("validated writer",),
        ),
    )
    registry = CapabilityRegistry(reports)

    assert tuple(report.state for report in registry.snapshot()) == (
        CapabilityState.DEGRADED,
        CapabilityState.AVAILABLE,
        CapabilityState.UNAVAILABLE,
    )
    assert registry.is_available("format.esp.read") is True
    assert registry.is_available("archive.rar.write") is False


def test_unregistered_capability_is_explicitly_unavailable() -> None:
    report = CapabilityRegistry().report("missing.capability")

    assert report.state is CapabilityState.UNAVAILABLE
    assert report.missing_prerequisites == ("registration",)
    assert report.reasons


def test_capability_report_round_trip_and_replacement() -> None:
    capability = CapabilityId("search.semantic")
    unavailable = CapabilityReport(
        capability,
        CapabilityState.UNAVAILABLE,
        missing_prerequisites=("sentence-transformers",),
        metadata=(("adapter", "semantic-index"),),
    )
    registry = CapabilityRegistry((unavailable,))
    registry.register(CapabilityReport(capability, CapabilityState.AVAILABLE))

    restored = CapabilityReport.from_dict(unavailable.to_dict())

    assert restored == unavailable
    assert registry.report(capability).state is CapabilityState.AVAILABLE


def test_capability_state_invariants_are_enforced() -> None:
    with pytest.raises(ValueError):
        CapabilityReport(CapabilityId("x"), CapabilityState.UNAVAILABLE)
    with pytest.raises(ValueError):
        CapabilityReport(
            CapabilityId("x"),
            CapabilityState.AVAILABLE,
            missing_prerequisites=("dependency",),
        )


def test_contract_schemas_expose_version_and_closed_outcomes() -> None:
    operation_schema = operation_result_json_schema()
    capability_schema = capability_report_json_schema()

    assert operation_schema["properties"]["schema_version"] == {"const": 1}
    assert operation_schema["properties"]["outcome"]["enum"] == [
        "completed",
        "partial",
        "failed",
        "cancelled",
    ]
    assert capability_schema["properties"]["state"]["enum"] == [
        "available",
        "degraded",
        "unavailable",
    ]
