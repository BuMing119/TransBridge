from __future__ import annotations

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.terminology.ports import PageRequest
from transbridge.application.terminology_sync.models import TerminologySyncMode, TerminologySyncTarget
from transbridge.application.terminology_sync.plan_models import (
    TerminologySyncAction,
    TerminologySyncPlan,
    TerminologySyncPlanItem,
    TerminologySyncReason,
)
from transbridge.application.terminology_sync.service import (
    TerminologySyncApplicationService,
    TerminologySyncPreflight,
)
from transbridge.application.terminology_sync.use_case import CreateTerminologySyncPlanRequest


class _Contexts:
    target = TerminologySyncTarget("https://paratranz.cn/api", 7, 123)

    def preflight(self, context, mode):
        return TerminologySyncPreflight(
            mode,
            True,
            context.project_id,
            context.variant_id,
            "version-1",
            "local-digest",
            self.target,
            mapping_status="ready",
        )

    def planning_request(self, context, mode):
        return CreateTerminologySyncPlanRequest(context.project_id, context.variant_id, self.target, mode, 4)

    def activate_mapping(self, context, mode, *, replace_existing=False):
        del replace_existing
        return self.preflight(context, mode)


class _Planning:
    def __init__(self, plan):
        self.plan = plan

    def create_plan(self, request):
        return self.plan


class _Unused:
    pass


def _plan() -> TerminologySyncPlan:
    target = _Contexts.target
    items = tuple(
        TerminologySyncPlanItem(
            f"item-{index}",
            TerminologySyncAction.CREATE_REMOTE,
            TerminologySyncReason.LOCAL_ONLY,
            local_term_id=f"term-{index}",
        )
        for index in range(3)
    )
    return TerminologySyncPlan(
        "line-1",
        target.target_id,
        4,
        2,
        TerminologySyncMode.BACKUP,
        "project-1",
        "variant-1",
        "version-1",
        "local-digest",
        "remote-digest",
        0,
        items,
    )


def test_shared_service_keeps_one_plan_ref_and_pages_bounded_items() -> None:
    service = TerminologySyncApplicationService(
        contexts=_Contexts(),
        planning=_Planning(_plan()),
        tasks=_Unused(),
        runtime=_Unused(),
    )
    context = RequestContext(owner_id="owner-1", project_id="project-1", variant_id="variant-1")

    summary = service.create_plan(context, TerminologySyncMode.BACKUP)
    first = service.page_plan(summary.ref, PageRequest(limit=2))
    second = service.page_plan(summary.ref, PageRequest(limit=2, cursor=first.next_cursor))

    assert summary.ref.plan_hash == first.snapshot_digest
    assert summary.counts == (("create_remote", 3),)
    assert [item.item_id for item in first.items] == ["item-0", "item-1"]
    assert [item.item_id for item in second.items] == ["item-2"]
    assert second.next_cursor is None


def test_bidirectional_plan_is_visible_but_not_executable_without_s05_task_entrypoint() -> None:
    plan = _plan()
    bidirectional = TerminologySyncPlan(
        plan.line_id,
        plan.target_identity,
        plan.binding_revision,
        plan.profile_revision,
        TerminologySyncMode.BIDIRECTIONAL,
        plan.local_project_id,
        plan.local_variant_id,
        plan.local_version_id,
        plan.local_content_digest,
        plan.remote_snapshot_digest,
        plan.baseline_revision,
        plan.items,
    )
    service = TerminologySyncApplicationService(
        contexts=_Contexts(),
        planning=_Planning(bidirectional),
        tasks=_Unused(),
        runtime=_Unused(),
    )
    context = RequestContext(owner_id="owner-1", project_id="project-1", variant_id="variant-1")

    summary = service.create_plan(context, TerminologySyncMode.BIDIRECTIONAL)

    assert not summary.execution_available


def test_mapping_replacement_confirmation_is_invalid_after_target_drift() -> None:
    contexts = _Contexts()
    service = TerminologySyncApplicationService(
        contexts=contexts,
        planning=_Planning(_plan()),
        tasks=_Unused(),
        runtime=_Unused(),
    )
    context = RequestContext(owner_id="owner-1", project_id="project-1", variant_id="variant-1")
    token = service.issue_mapping_replacement_confirmation(context, TerminologySyncMode.BACKUP)

    contexts.target = TerminologySyncTarget("https://other.example/api", 8, 456)

    with pytest.raises(PermissionError):
        service.activate_mapping(context, TerminologySyncMode.BACKUP, token)
