from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from tests.application.terminology_sync import test_draft_import as inbound_data
from tests.application.terminology_sync.test_service import _Contexts, _plan, _Planning
from tests.application.terminology_sync.test_task_failure_feedback import runtime_service
from transbridge.application.contracts import RequestContext
from transbridge.application.security.hitl import ConfirmationAuthority
from transbridge.application.tasks import JobSpec, JobState, TaskAccessError
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemOutcome,
    TerminologySyncItemStatus,
    TerminologySyncRetryToken,
)
from transbridge.application.terminology_sync.inbound import InboundChangeKind, InboundReviewDecision
from transbridge.application.terminology_sync.inbound_service import DurableTerminologyInboundService
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.service import TerminologySyncApplicationService, owner_from_context
from transbridge.application.terminology_sync.use_case import (
    TerminologySyncPlanningUseCase,
    TerminologySyncPlanStaleError,
)
from transbridge.application.tools.contracts import ToolInvocation
from transbridge.bootstrap.terminology import _ProductionInboundApplication
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_terminology_sync  # noqa: F401
from transbridge.smart_assistant.tools.base import execute_with_guardrails
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.tools.terminology.inbound_review import InboundReviewEdit
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter

CONTEXT = RequestContext(owner_id="owner-1", project_id="project-1", variant_id="variant-1")
MODE = TerminologySyncMode.BACKUP


def _entries(service, context=CONTEXT):
    assistant = ExecutionContext(request_context=context, confirmation_authority=ConfirmationAuthority())
    assistant.terminology_sync_service = service
    desktop = TerminologySyncPresenter(service, context, owner_from_context(context, entrypoint="gui"))
    return desktop, assistant


def _call(context, operation, arguments, *, confirm=False):
    name = f"terminology_sync_{operation}"
    if confirm:
        owner = context.request_context.owner_id
        invocation = ToolInvocation(name, arguments, owner_id=owner, plan_hash=context.plan_hash)
        context.confirmation_token = context.confirmation_authority.issue(
            owner_id=owner, request_hash=invocation.request_hash
        )
    return execute_with_guardrails(ToolRegistry.get(name), arguments, context)


def _data(response):
    assert response.success, response.message
    return response.data


class _MappingContext(_Contexts):
    def __init__(self):
        self.replacements = []

    def preflight(self, context, mode):
        result = super().preflight(context, mode)
        if not self.replacements:
            return replace(result, available=False, diagnostics=("目标已映射到另一 Variant，需要确认替换同步映射",))
        return result

    def activate_mapping(self, context, mode, *, replace_existing=False):
        if not replace_existing:
            raise PermissionError("replace the existing mapping explicitly")
        self.replacements.append((context.project_id, context.variant_id))
        return self.preflight(context, mode)


def test_preflight_mapping_confirmation_and_replacement_match_desktop():
    mappings = [_MappingContext(), _MappingContext()]
    desktop, _ = _entries(
        TerminologySyncApplicationService(contexts=mappings[0], planning=None, runtime=None, tasks=None)
    )
    _, assistant = _entries(
        TerminologySyncApplicationService(contexts=mappings[1], planning=None, runtime=None, tasks=None)
    )
    args = {"mode": MODE.value}
    state = desktop.preflight(MODE)
    preflight = _data(_call(assistant, "preflight", args))
    assert preflight["available"] == state.preflight.available is False
    assert preflight["diagnostics"] == list(state.preflight.diagnostics)
    assert not _call(assistant, "activate_mapping", args).success
    assert not _call(assistant, "mapping_confirmation", args).success

    token = _data(_call(assistant, "mapping_confirmation", args, confirm=True))
    state = desktop.activate_mapping()
    activation = dict(args, replacement_token=token)
    activated = _data(_call(assistant, "activate_mapping", activation))
    assert activated["available"] == state.preflight.available is True
    assert activated["target"] == asdict(state.preflight.target)
    assert mappings[0].replacements == mappings[1].replacements == [("project-1", "variant-1")]
    assert not _call(assistant, "activate_mapping", activation).success


def test_plan_and_saved_cursor_pages_use_actual_desktop_page_size():
    plan = _plan()
    items = tuple(replace(plan.items[0], item_id=f"item-{index:03d}") for index in range(104))
    planning = _Planning(replace(plan, items=items, plan_hash=""))
    service = TerminologySyncApplicationService(contexts=_Contexts(), planning=planning, tasks=None, runtime=None)
    desktop, assistant = _entries(service)
    desktop.preflight(MODE)
    first = desktop.create_plan()
    data = _data(_call(assistant, "plan", {"mode": MODE.value}))
    assert data["ref"] == asdict(first.summary.ref)
    assert data["counts"] == [list(pair) for pair in first.summary.counts]
    assert data["total"] == first.plan_total == 104
    assert [entry["item_id"] for entry in data["items"]] == [item.item_id for item in first.items]
    assert len(first.items) == 100 and data["next_cursor"] == first.next_cursor.encode()
    args = dict(data["ref"], cursor=data["next_cursor"])
    second = desktop.load_next_page()
    page = _data(_call(assistant, "plan_page", args))
    assert len(page["items"]) == len(second.items) == 4
    assert [entry["item_id"] for entry in page["items"]] == [item.item_id for item in second.items]
    assert page["plan_hash"] == first.summary.ref.plan_hash
    assert page["next_cursor"] is second.next_cursor is None
    assert desktop.load_previous_page().items == first.items
    assert not _call(assistant, "plan_page", dict(args, cursor="invalid")).success
    assert _call(assistant, "plan", {"mode": MODE.value, "limit": 2}).error_code == "ARGUMENT_SCHEMA_INVALID"
    assert _call(assistant, "plan_page", dict(args, limit=2)).error_code == "ARGUMENT_SCHEMA_INVALID"


class _FixedPlan(TerminologySyncPlanningUseCase):
    """Keep all authorization and freshness checks, replace remote plan inputs."""

    def __init__(self, plan):
        super().__init__(inputs=None)
        self.plan = plan

    def create_plan(self, request):
        return self.plan


def test_plan_scope_and_changed_remote_inputs_invalidate_an_authorized_execution():
    plan = _plan()
    planning = _FixedPlan(replace(plan, items=(replace(plan.items[0], requires_review=True),), plan_hash=""))
    service = TerminologySyncApplicationService(contexts=_Contexts(), planning=planning, tasks=None, runtime=None)
    desktop, assistant = _entries(service)
    desktop.preflight(MODE)
    ref = asdict(desktop.create_plan().summary.ref)
    desktop.confirm()
    _, other = _entries(service, replace(CONTEXT, owner_id="other"))
    assert not _call(other, "authorize", ref, confirm=True).success
    token = _data(_call(assistant, "authorize", ref, confirm=True))
    planning.plan = replace(planning.plan, remote_snapshot_digest="changed", plan_hash="")
    with pytest.raises(TerminologySyncPlanStaleError):
        desktop.execute()
    failure = _call(assistant, "execute", dict(ref, confirmation_token=token))
    assert not failure.success and failure.error_code == TerminologySyncPlanStaleError.code


@pytest.mark.parametrize("status", ["succeeded", "failed", "unknown", "exception"])
def test_confirmed_execution_status_and_recovery_use_only_owned_job_receipts(status):
    original = _plan()
    plan = replace(original, items=(replace(original.items[0], requires_review=True),), plan_hash="")
    received = []

    def run(request):
        if status == "exception":
            raise RuntimeError("远端快照已经过期")
        received.append((request.authorized_plan.confirmation_code, request.retry_token))
        if request.retry_token is not None:
            return TerminologyBackupExecutionResult(request.run_id, plan.plan_hash, ())
        item = plan.items[0]
        unknown = status == "unknown"
        token = None
        if status != "succeeded":
            token = TerminologySyncRetryToken(
                plan.line_id,
                plan.target_identity,
                plan.plan_hash,
                CONTEXT.owner_id,
                (),
                (item.item_id,) if unknown else (),
            )
        outcome = TerminologySyncItemOutcome(
            item.item_id, item.action, TerminologySyncItemStatus(status), "TEST", status
        )
        return TerminologyBackupExecutionResult(request.run_id, plan.plan_hash, (outcome,), token, unknown)

    runtime, _, _, entrypoint, _, _ = runtime_service(SimpleNamespace(execute=run, reconcile=run), plan)
    try:
        planning = _FixedPlan(plan)
        service = TerminologySyncApplicationService(
            contexts=_Contexts(), planning=planning, tasks=entrypoint, runtime=runtime
        )
        desktop, assistant = _entries(service)
        desktop.preflight(MODE)
        summary = desktop.create_plan().summary
        ref = _data(_call(assistant, "plan", {"mode": MODE.value}))["ref"]
        assert ref == asdict(summary.ref)
        assert not desktop.state.can_execute and not _call(assistant, "execute", ref).success
        assert not _call(assistant, "authorize", ref).success
        desktop_token = desktop.confirm().confirmation_token
        token = _data(_call(assistant, "authorize", ref, confirm=True))
        assert (token["request_hash"], token["owner_id"]) == (desktop_token.request_hash, desktop_token.owner_id)
        desktop_ref = desktop.execute()
        execute = dict(ref, confirmation_token=token)
        job = _data(_call(assistant, "execute", execute))
        assert not _call(assistant, "execute", execute).success
        state = desktop.complete_job(desktop_ref)
        report = _data(_call(assistant, "status", {"job": job}))
        assert job["owner_id"] == desktop_ref.owner_id
        assert report["snapshot"]["state"] == state.job_state.value
        _, foreign = _entries(service, replace(CONTEXT, session_id="other"))
        for op in ("status", "retry", "reconcile"):
            assert not _call(foreign, op, {"job": job}).success
            assert _call(assistant, op, {"job": dict(job, retry_token={})}).error_code == "ARGUMENT_SCHEMA_INVALID"
        if status == "exception":
            assert report["failure"] == asdict(state.failure)
            assert report["failure"]["message"] == "远端快照已经过期"
            assert not _call(assistant, "retry", {"job": job}).success
            return
        assert report["result"]["outcomes"] == [asdict(item) for item in state.result.outcomes]
        rows = state.result_projection.page(0, state.result_projection.total)
        assert report["rows"] == [{"item_id": row.item_id, "cells": list(row.cells)} for row in rows]
        if status == "succeeded":
            assert state.job_state is JobState.COMPLETED
            assert not state.can_retry and not _call(assistant, "retry", {"job": job}).success
            assert not state.can_reconcile and not _call(assistant, "reconcile", {"job": job}).success
            return
        assert report["result"]["retry_token"]["token_digest"] == state.retry_token.token_digest
        operation, denied = ("reconcile", "retry") if status == "unknown" else ("retry", "reconcile")
        with pytest.raises(RuntimeError):
            getattr(desktop, denied)()
        assert not _call(assistant, denied, {"job": job}).success
        assert _call(assistant, operation, {"retry_token": report["result"]["retry_token"]}).error_code == (
            "ARGUMENT_SCHEMA_INVALID"
        )
        next_desktop = getattr(desktop, operation)()
        next_job = _data(_call(assistant, operation, {"job": job}))
        assert received[-1] == received[-2] == (operation.upper(), state.retry_token)
        assert (
            _data(_call(assistant, "status", {"job": next_job}))["snapshot"]["state"]
            == desktop.complete_job(next_desktop).job_state.value
        )
    finally:
        runtime.close()


def test_status_rejects_an_unrelated_queued_job_even_in_the_same_task_scope():
    runtime, desktop_owner, _, _, service, _ = runtime_service(None)
    try:
        desktop, assistant = _entries(service)
        unrelated = JobSpec("operation.unrelated", "source", "input")
        desktop_job = runtime.submit(unrelated, desktop_owner).ref
        job = runtime.submit(unrelated, owner_from_context(CONTEXT, entrypoint="smart-assistant")).ref
        with pytest.raises(TaskAccessError, match="unknown terminology sync job"):
            desktop.complete_job(desktop_job)
        report = _call(assistant, "status", {"job": job.to_dict()})
        assert not report.success and report.error_code == "job_not_found"
    finally:
        runtime.close()


def test_inbound_listing_and_pages_match_desktop_for_default_last_and_out_of_range_offsets():
    changes = inbound_data._change_set(*(["add"] * 55))
    _, store, _, _ = inbound_data._service(changes)
    store.list_change_sets = lambda project, variant: (
        (changes,) if (project, variant) == (changes.project_id, changes.variant_id) else ()
    )
    inbound = DurableTerminologyInboundService(store, importer=None)
    service = TerminologySyncApplicationService(contexts=None, planning=None, tasks=None, runtime=None, inbound=inbound)
    desktop, assistant = _entries(service)
    state = desktop.load_inbound()
    listing = _data(_call(assistant, "inbound", {}))["change_sets"]
    assert listing == [
        {"change_set_id": item.change_set_id, "created_at": item.created_at.isoformat(), "total": len(item.items)}
        for item in state.inbound_sets
    ]
    args = {"change_set_id": state.selected_inbound_id}
    for offset in (0, 50, 9999):
        state = desktop.page_inbound(offset)
        data = _data(_call(assistant, "inbound_get", dict(args, offset=offset)))
        assert data["total"] == state.inbound_total == 55
        assert data["offset"] == state.inbound_offset == min(offset, 54)
        assert [item["item_id"] for item in data["items"]] == [item.item_id for item in state.inbound_items]
        assert data["next_offset"] == (50 if offset == 0 else None)
    assert _call(assistant, "inbound_get", dict(args, limit=1)).error_code == "ARGUMENT_SCHEMA_INVALID"
    other_desktop, other_assistant = _entries(service, replace(CONTEXT, project_id="other"))
    assert other_desktop.load_inbound().inbound_sets == ()
    assert _data(_call(other_assistant, "inbound", {}))["change_sets"] == []
    with pytest.raises(KeyError):
        other_desktop.select_inbound(changes.change_set_id)
    assert not _call(other_assistant, "inbound_get", args).success


def test_inbound_review_displays_the_same_conflicts_and_commits_the_same_audited_draft():
    changes = inbound_data._change_set("add", "add", "conflict")
    effective = (inbound_data._decision("Sword", "剑"),)

    def make_service():
        importer, store, transactions, state = inbound_data._service(changes, effective=effective)
        store.list_change_sets = lambda _project, _variant: (changes,)
        durable = DurableTerminologyInboundService(store, importer)
        reader = SimpleNamespace(read_line=lambda _conn, project, variant: state.current_line(project, variant))
        inbound = _ProductionInboundApplication(store, transactions, reader, durable)
        service = TerminologySyncApplicationService(
            contexts=None, planning=None, tasks=None, runtime=None, inbound=inbound
        )
        return service, store, transactions, state

    desktop_service, desktop_store, desktop_drafts, desktop_state = make_service()
    assistant_service, assistant_store, assistant_drafts, assistant_state = make_service()
    desktop, _ = _entries(desktop_service)
    _, assistant = _entries(assistant_service)
    state = desktop.load_inbound()
    edits = tuple(
        InboundReviewEdit(item, InboundReviewDecision.EDIT, " 新盾 ")
        if item.remote is not None and item.remote.original == "Shield 2"
        else InboundReviewEdit.initial(item)
        for item in state.inbound_items
    )

    def preview(selected):
        desktop_result = desktop.inbound_preview_request(selected)()
        choices = [
            dict(item_id=edit.item.item_id, decision="edit", translation=edit.translation)
            if edit.decision is InboundReviewDecision.EDIT
            else dict(item_id=edit.item.item_id, decision=edit.decision.value)
            for edit in selected
        ]
        args = {"change_set_id": state.selected_inbound_id, "choices": choices}
        result = _data(_call(assistant, "inbound_preview", args))
        proposal = desktop_result.inbound_proposal
        projection = desktop_result.inbound_projection
        assert result["proposal_digest"] == proposal.proposal_digest
        assert result["counts"] == [list(pair) for pair in proposal.counts]
        assert result["diagnostics"] == list(proposal.diagnostics)
        assert result["changes"] == [
            {"item_id": row.item_id, "cells": list(row.cells)} for row in projection.page(0, projection.total)
        ]
        assert result["committable"] == proposal.committable
        return result

    blocked = preview(edits)
    assert not blocked["committable"]
    with pytest.raises(RuntimeError, match="阻断项"):
        desktop.commit_inbound()
    assert not _call(assistant, "inbound_commit", {"proposal_digest": blocked["proposal_digest"]}, confirm=True).success
    assert desktop_drafts.value is assistant_drafts.value is None
    assert _call(assistant, "inbound_preview", {"selection": {}}).error_code == "ARGUMENT_SCHEMA_INVALID"
    edits = tuple(
        replace(edit, decision=InboundReviewDecision.REJECT)
        if edit.item.kind is InboundChangeKind.REMOTE_CONFLICT
        else edit
        for edit in edits
    )
    previewed = preview(edits)
    assert previewed["committable"]
    ref = {"proposal_digest": previewed["proposal_digest"]}
    assert _call(assistant, "inbound_commit", ref).error_code == "CONFIRMATION_REQUIRED"
    assert assistant_drafts.value is None
    committed = desktop.commit_inbound().inbound_commit
    result = _data(_call(assistant, "inbound_commit", ref, confirm=True))
    assert result["draft_ref"] == asdict(committed.draft_ref)
    assert desktop_store.review == assistant_store.review == committed.review_state
    assert desktop_drafts.value == assistant_drafts.value
    assert {decision.translation for decision in assistant_drafts.value.decisions} == {"剑", "盾", "新盾"}
    assert desktop_state.decisions == assistant_state.decisions == effective
