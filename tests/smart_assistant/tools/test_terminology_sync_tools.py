from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_terminology_sync  # noqa: F401


def test_terminology_sync_tools_are_independent_and_registered_for_mcp_projection() -> None:
    names = {spec.name for spec in ToolRegistry.list_namespace("terminology_sync")}

    assert names == {
        "terminology_sync_preflight",
        "terminology_sync_plan",
        "terminology_sync_activate_mapping",
        "terminology_sync_mapping_confirmation",
        "terminology_sync_plan_page",
        "terminology_sync_authorize",
        "terminology_sync_execute",
        "terminology_sync_status",
        "terminology_sync_retry",
        "terminology_sync_reconcile",
        "terminology_sync_inbound",
        "terminology_sync_inbound_get",
        "terminology_sync_inbound_preview",
        "terminology_sync_inbound_commit",
    }
    assert ToolRegistry.get("terminology_sync_execute").permission == "write"
    assert all(
        spec.parameters["additionalProperties"] is False for spec in ToolRegistry.list_namespace("terminology_sync")
    )


def test_issuing_authorization_and_recording_manual_review_require_a_human_confirmation():
    from transbridge.smart_assistant.guardrails.permission import PermissionGuard
    from transbridge.smart_assistant.tools.types import ExecutionContext

    for name in (
        "terminology_sync_authorize",
        "terminology_sync_mapping_confirmation",
        "terminology_sync_inbound_commit",
    ):
        guard = PermissionGuard().before_execute({"tool": name, "args": {}}, ExecutionContext())
        assert not guard.allowed and guard.code == "CONFIRMATION_REQUIRED"


def test_inbound_preview_matches_desktop_and_stale_or_foreign_proposals_cannot_write():
    from dataclasses import replace
    from types import SimpleNamespace

    import pytest

    from tests.application.terminology_sync import test_draft_import as fixture
    from transbridge.application.tasks import OwnerRef
    from transbridge.application.terminology.drafts import new_draft, revised_draft
    from transbridge.application.terminology_sync.draft_import_models import DraftImportStaleError
    from transbridge.application.terminology_sync.inbound import InboundReviewDecision
    from transbridge.application.terminology_sync.inbound_service import DurableTerminologyInboundService
    from transbridge.application.terminology_sync.service import TerminologySyncApplicationService
    from transbridge.bootstrap.terminology import _ProductionInboundApplication
    from transbridge.ui.tools.terminology.inbound_review import InboundReviewEdit
    from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter

    change_set = fixture._change_set("add")
    active = new_draft(
        draft_id="active",
        project_id="project-1",
        variant_id="variant-1",
        base_version_id="version-1",
        base_content_digest="effective-digest",
    )
    importer, store, transactions, state = fixture._service(change_set, active=active)
    store.list_change_sets = lambda _project, _variant: (change_set,)
    durable = DurableTerminologyInboundService(store, importer)
    reader = SimpleNamespace(read_line=lambda _conn, project, variant: state.current_line(project, variant))
    inbound = _ProductionInboundApplication(store, transactions, reader, durable)
    service = TerminologySyncApplicationService(contexts=None, planning=None, tasks=None, runtime=None, inbound=inbound)
    context = fixture._context()
    tools = SimpleNamespace(request_context=context, terminology_sync_service=service)
    presenter = TerminologySyncPresenter(service, context, OwnerRef(context.owner_id, "gui"))
    item = presenter.load_inbound().inbound_items[0]
    edit = InboundReviewEdit(item, InboundReviewDecision.EDIT, "  新译文  ")
    proposal = presenter.inbound_preview_request((edit,))().inbound_proposal
    preview = ToolRegistry.get("terminology_sync_inbound_preview")
    commit = ToolRegistry.get("terminology_sync_inbound_commit")
    args = {
        "change_set_id": change_set.change_set_id,
        "choices": [{"item_id": item.item_id, "decision": "edit", "translation": edit.translation}],
    }
    result = preview.execute(args, tools)
    assert result.success and result.data["committable"]
    assert result.data["proposal_digest"] == proposal.proposal_digest
    assert proposal.selection.draft_expectation.draft_revision == active.ref.revision
    assert transactions.save_count == transactions.create_count == 0

    ref = {"proposal_digest": proposal.proposal_digest}
    tools.request_context = replace(context, variant_id="other")
    assert preview.execute(args, tools).error_category == "permission"
    assert commit.execute(ref, tools).error_category == "permission"
    with pytest.raises(PermissionError):
        service.commit_import_ref(ref["proposal_digest"], tools.request_context)

    tools.request_context = context
    assert not preview.execute({"selection": {"expected_review_revision": 0}}, tools).success
    transactions.value = revised_draft(active, digest_context={"changed": True})
    with pytest.raises(DraftImportStaleError):
        service.commit_import_ref(ref["proposal_digest"], context)
    assert transactions.save_count == 0
    fresh = preview.execute(args, tools)
    assert fresh.success and fresh.data["proposal_digest"] != ref["proposal_digest"]
    service.commit_import_ref(fresh.data["proposal_digest"], context)
    assert transactions.value.decisions[0].translation == "新译文"
