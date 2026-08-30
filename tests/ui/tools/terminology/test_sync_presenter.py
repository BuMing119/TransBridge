from __future__ import annotations

from types import SimpleNamespace

import pytest

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import OwnerRef
from transbridge.application.terminology_sync.draft_import_models import DraftImportChoice
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemOutcome,
    TerminologySyncItemStatus,
    TerminologySyncRetryToken,
)
from transbridge.application.terminology_sync.inbound import InboundReviewDecision
from transbridge.application.terminology_sync.plan_models import TerminologySyncAction
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter


class _SyncService:
    def __init__(self, result: TerminologyBackupExecutionResult) -> None:
        self.execution_result = result
        self.status_refs: list[JobRef] = []
        self.result_refs: list[JobRef] = []
        self.retry_tokens: list[TerminologySyncRetryToken] = []
        self.reconcile_tokens: list[TerminologySyncRetryToken] = []
        self.inbound_sets: tuple[object, ...] = ()
        self.import_choices: tuple[DraftImportChoice, ...] = ()
        self.proposal = SimpleNamespace(proposal_digest="proposal-1", committable=True)
        self.committed = SimpleNamespace(proposal_digest="proposal-1")

    def status(self, ref: JobRef, actor: OwnerRef) -> object:
        del actor
        self.status_refs.append(ref)
        return SimpleNamespace(is_terminal=True)

    def result(self, ref: JobRef, actor: OwnerRef) -> TerminologyBackupExecutionResult:
        del actor
        self.result_refs.append(ref)
        return self.execution_result

    def retry(self, token: TerminologySyncRetryToken, actor: OwnerRef) -> JobRef:
        self.retry_tokens.append(token)
        return JobRef("retry-job", actor.owner_id, "retry-run")

    def reconcile(self, token: TerminologySyncRetryToken, actor: OwnerRef) -> JobRef:
        self.reconcile_tokens.append(token)
        return JobRef("reconcile-job", actor.owner_id, "reconcile-run")

    def list_inbound(self, context: RequestContext) -> tuple[object, ...]:
        del context
        return self.inbound_sets

    def prepare_import_selection(
        self,
        context: RequestContext,
        change_set_id: str,
        choices: tuple[DraftImportChoice, ...],
    ) -> object:
        del context
        self.import_choices = choices
        return SimpleNamespace(change_set_id=change_set_id, choices=choices)

    def preview_import(self, selection: object) -> object:
        del selection
        return self.proposal

    def commit_import(self, proposal: object, context: RequestContext) -> object:
        del proposal, context
        return self.committed


def _presenter(service: _SyncService) -> TerminologySyncPresenter:
    return TerminologySyncPresenter(
        service,  # type: ignore[arg-type]
        RequestContext("owner", project_id="project", variant_id="variant"),
        OwnerRef("owner", "gui", "project", "variant"),
    )


def _result(*, unknown: bool) -> TerminologyBackupExecutionResult:
    status = TerminologySyncItemStatus.UNKNOWN if unknown else TerminologySyncItemStatus.FAILED
    token = TerminologySyncRetryToken(
        "line-1",
        "target-1",
        "plan-hash",
        "owner",
        (),
        ("item-1",) if unknown else (),
    )
    return TerminologyBackupExecutionResult(
        "run-1",
        "plan-hash",
        (
            TerminologySyncItemOutcome(
                "item-1",
                TerminologySyncAction.CREATE_REMOTE,
                status,
                "REMOTE_UNKNOWN" if unknown else "REMOTE_FAILED",
                "remote outcome",
            ),
        ),
        token,
        reconcile_required=unknown,
    )


def test_terminal_job_loads_result_and_unknown_outcomes_only_allow_reconcile() -> None:
    service = _SyncService(_result(unknown=True))
    presenter = _presenter(service)
    ref = JobRef("job-1", "owner", "run-1")

    state = presenter.complete_job(ref)

    assert service.status_refs == [ref]
    assert service.result_refs == [ref]
    assert state.job_ref == ref
    assert state.result is service.execution_result
    assert not state.can_retry
    assert state.can_reconcile
    with pytest.raises(RuntimeError, match="reconciled before retry"):
        presenter.retry()
    assert presenter.reconcile() == JobRef("reconcile-job", "owner", "reconcile-run")
    assert service.reconcile_tokens == [service.execution_result.retry_token]


def test_confirmed_failures_can_retry_but_cannot_reconcile() -> None:
    service = _SyncService(_result(unknown=False))
    presenter = _presenter(service)
    presenter.complete_job(JobRef("job-1", "owner", "run-1"))

    assert presenter.state.can_retry
    assert not presenter.state.can_reconcile
    with pytest.raises(RuntimeError, match="only for unknown"):
        presenter.reconcile()
    assert presenter.retry() == JobRef("retry-job", "owner", "retry-run")
    assert service.retry_tokens == [service.execution_result.retry_token]


def test_inbound_review_keeps_one_page_and_retains_preview_until_commit() -> None:
    service = _SyncService(_result(unknown=False))
    service.inbound_sets = (SimpleNamespace(change_set_id="set-1", items=("item-1", "item-2", "item-3")),)
    presenter = _presenter(service)

    first = presenter.load_inbound(page_size=2)
    second = presenter.page_inbound(2, page_size=2)
    choice = DraftImportChoice("item-3", InboundReviewDecision.REJECT)
    previewed = presenter.preview_inbound((choice,))
    committed = presenter.commit_inbound()

    assert first.inbound_items == ("item-1", "item-2")
    assert second.inbound_items == ("item-3",)
    assert second.inbound_total == 3
    assert service.import_choices == (choice,)
    assert previewed.inbound_proposal is service.proposal
    assert committed.inbound_proposal is service.proposal
    assert committed.inbound_commit is service.committed
