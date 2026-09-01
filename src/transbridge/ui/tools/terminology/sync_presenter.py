"""Qt-free presentation state for terminology synchronization."""

from __future__ import annotations

from dataclasses import dataclass, replace

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.security.hitl import ConfirmationToken
from transbridge.application.tasks import JobSnapshot, OwnerRef
from transbridge.application.terminology.ports import PageRequest, SnapshotCursor
from transbridge.application.terminology_sync.draft_import_models import (
    DraftImportChoice,
    DraftImportCommitResult,
    DraftImportProposal,
)
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncRetryToken,
)
from transbridge.application.terminology_sync.inbound import InboundTerminologyChange, InboundTerminologyChangeSet
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.plan_models import TerminologySyncPlanItem
from transbridge.application.terminology_sync.service import (
    TerminologySyncApplicationService,
    TerminologySyncPlanSummary,
    TerminologySyncPreflight,
)


@dataclass(frozen=True, slots=True)
class TerminologySyncViewState:
    mode: TerminologySyncMode | None = None
    busy: bool = False
    preflight: TerminologySyncPreflight | None = None
    summary: TerminologySyncPlanSummary | None = None
    items: tuple[TerminologySyncPlanItem, ...] = ()
    next_cursor: SnapshotCursor | None = None
    confirmation_copy: str | None = None
    confirmation_token: ConfirmationToken | None = None
    job: JobSnapshot | None = None
    job_ref: JobRef | None = None
    result: TerminologyBackupExecutionResult | None = None
    inbound_sets: tuple[InboundTerminologyChangeSet, ...] = ()
    selected_inbound_id: str | None = None
    inbound_items: tuple[InboundTerminologyChange, ...] = ()
    inbound_offset: int = 0
    inbound_total: int = 0
    inbound_proposal: DraftImportProposal | None = None
    inbound_commit: DraftImportCommitResult | None = None
    error: str | None = None
    inbound_notice: str = "远端变化导入草稿后仍待复核与发布，不会影响当前翻译术语版本。"

    @property
    def can_execute(self) -> bool:
        summary = self.summary
        if summary is None or summary.blocked or summary.has_conflicts or self.busy or not summary.execution_available:
            return False
        return not summary.requires_confirmation or self.confirmation_token is not None

    @property
    def retry_token(self) -> TerminologySyncRetryToken | None:
        return None if self.result is None else self.result.retry_token

    @property
    def can_retry(self) -> bool:
        token = self.retry_token
        return token is not None and not token.unknown_item_ids and not self.busy

    @property
    def can_reconcile(self) -> bool:
        token = self.retry_token
        return token is not None and bool(token.unknown_item_ids) and not self.busy


class TerminologySyncPresenter:
    BACKUP_ACTION = "备份已发布版本"
    BIDIRECTIONAL_ACTION = "双向同步"

    def __init__(
        self,
        service: TerminologySyncApplicationService,
        context: RequestContext,
        owner: OwnerRef,
    ) -> None:
        self._service = service
        self._context = context
        self._owner = owner
        self._state = TerminologySyncViewState()
        self._inbound_generation = 0

    @property
    def state(self) -> TerminologySyncViewState:
        return self._state

    def preflight(self, mode: TerminologySyncMode) -> TerminologySyncViewState:
        mode = TerminologySyncMode(mode)
        result = self._service.preflight(self._context, mode)
        self._state = TerminologySyncViewState(mode=mode, preflight=result)
        return self._state

    def create_plan(self) -> TerminologySyncViewState:
        if self._state.mode is None:
            raise RuntimeError("select backup or bidirectional mode before planning")
        try:
            summary = self._service.create_plan(self._context, self._state.mode)
            page = self._service.page_plan(summary.ref, PageRequest())
        except Exception as exc:
            self._state = replace(self._state, busy=False, error=str(exc), confirmation_token=None)
            return self._state
        self._state = replace(
            self._state,
            summary=summary,
            items=page.items,
            next_cursor=page.next_cursor,
            confirmation_copy=_confirmation_copy(summary),
            confirmation_token=None,
            error=None,
        )
        return self._state

    def activate_mapping(self) -> TerminologySyncViewState:
        if self._state.mode is None:
            raise RuntimeError("select backup or bidirectional mode before activating a mapping")
        replacing = self._state.preflight is not None and any(
            "另一 Variant" in diagnostic for diagnostic in self._state.preflight.diagnostics
        )
        token = (
            self._service.issue_mapping_replacement_confirmation(self._context, self._state.mode) if replacing else None
        )
        result = self._service.activate_mapping(self._context, self._state.mode, token)
        self._state = TerminologySyncViewState(mode=self._state.mode, preflight=result)
        return self._state

    def load_inbound(self, *, page_size: int = 50) -> TerminologySyncViewState:
        values = self._service.list_inbound(self._context)
        self.invalidate_inbound_preview()
        selected = self._state.selected_inbound_id
        if selected not in {item.change_set_id for item in values}:
            selected = None if not values else values[0].change_set_id
        self._state = replace(
            self._state,
            inbound_sets=values,
            selected_inbound_id=selected,
            inbound_proposal=None,
            inbound_commit=None,
            error=None,
        )
        return self.page_inbound(0, page_size=page_size)

    def select_inbound(self, change_set_id: str, *, page_size: int = 50) -> TerminologySyncViewState:
        if change_set_id not in {item.change_set_id for item in self._state.inbound_sets}:
            raise KeyError("unknown inbound terminology change set")
        self.invalidate_inbound_preview()
        self._state = replace(
            self._state,
            selected_inbound_id=change_set_id,
            inbound_proposal=None,
            inbound_commit=None,
        )
        return self.page_inbound(0, page_size=page_size)

    def page_inbound(self, offset: int, *, page_size: int = 50) -> TerminologySyncViewState:
        selected = next(
            (item for item in self._state.inbound_sets if item.change_set_id == self._state.selected_inbound_id),
            None,
        )
        if selected is None:
            self._state = replace(self._state, inbound_items=(), inbound_offset=0, inbound_total=0)
            return self._state
        offset = max(0, min(offset, max(0, len(selected.items) - 1)))
        self._state = replace(
            self._state,
            inbound_items=selected.items[offset : offset + page_size],
            inbound_offset=offset,
            inbound_total=len(selected.items),
        )
        return self._state

    def preview_inbound(self, choices: tuple[DraftImportChoice, ...]) -> TerminologySyncViewState:
        if self._state.selected_inbound_id is None:
            raise RuntimeError("select an inbound terminology change set first")
        generation = self._inbound_generation
        selection = self._service.prepare_import_selection(
            self._context,
            self._state.selected_inbound_id,
            choices,
        )
        proposal = self._service.preview_import(selection)
        if generation != self._inbound_generation:
            return self._state
        self._state = replace(self._state, inbound_proposal=proposal, inbound_commit=None, error=None)
        return self._state

    def invalidate_inbound_preview(self) -> TerminologySyncViewState:
        self._inbound_generation += 1
        self._state = replace(self._state, inbound_proposal=None, inbound_commit=None, error=None)
        return self._state

    def commit_inbound(self) -> TerminologySyncViewState:
        proposal = self._state.inbound_proposal
        if proposal is None:
            raise RuntimeError("preview inbound draft changes before commit")
        committed = self._service.commit_import(proposal, self._context)
        self._state = replace(self._state, inbound_commit=committed, error=None)
        return self._state

    def load_next_page(self, *, limit: int = 100) -> TerminologySyncViewState:
        if self._state.summary is None or self._state.next_cursor is None:
            return self._state
        page = self._service.page_plan(
            self._state.summary.ref,
            PageRequest(limit=limit, cursor=self._state.next_cursor),
        )
        self._state = replace(
            self._state,
            items=page.items,
            next_cursor=page.next_cursor,
        )
        return self._state

    def confirm(self) -> TerminologySyncViewState:
        if self._state.summary is None:
            raise RuntimeError("create a terminology sync plan before confirmation")
        token = self._service.issue_confirmation(self._state.summary.ref, self._owner)
        self._state = replace(self._state, confirmation_token=token, error=None)
        return self._state

    def execute(self) -> JobRef:
        summary = self._state.summary
        if summary is None or not self._state.can_execute:
            raise RuntimeError("terminology sync plan is not executable")
        try:
            ref = self._service.execute(summary.ref, self._owner, self._state.confirmation_token)
        except Exception as exc:
            # Any freshness or authorization failure invalidates the one-use token.
            self._state = replace(self._state, confirmation_token=None, error=str(exc))
            raise
        self._state = replace(self._state, busy=True, confirmation_token=None, error=None)
        self._state = replace(self._state, job_ref=ref, result=None)
        return ref

    def complete_job(self, ref: JobRef) -> TerminologySyncViewState:
        snapshot = self._service.status(ref, self._owner)
        if not snapshot.is_terminal:
            self._state = replace(self._state, job=snapshot, job_ref=ref, busy=True)
            return self._state
        result = self._service.result(ref, self._owner)
        self._state = replace(self._state, job=snapshot, job_ref=ref, result=result, busy=False)
        return self._state

    def retry(self) -> JobRef:
        token = self._state.retry_token
        if token is None or token.unknown_item_ids:
            raise RuntimeError("unknown remote outcomes must be reconciled before retry")
        ref = self._service.retry(token, self._owner)
        self._state = replace(self._state, job_ref=ref, result=None, busy=True, error=None)
        return ref

    def reconcile(self) -> JobRef:
        token = self._state.retry_token
        if token is None or not token.unknown_item_ids:
            raise RuntimeError("reconcile is available only for unknown remote outcomes")
        ref = self._service.reconcile(token, self._owner)
        self._state = replace(self._state, job_ref=ref, result=None, busy=True, error=None)
        return ref

    def update_job(self, snapshot: JobSnapshot) -> TerminologySyncViewState:
        self._state = replace(self._state, job=snapshot, busy=not snapshot.is_terminal)
        return self._state

    def invalidate_plan(self, reason: str) -> TerminologySyncViewState:
        self._state = replace(
            self._state,
            summary=None,
            items=(),
            next_cursor=None,
            confirmation_token=None,
            confirmation_copy=None,
            error=reason,
        )
        return self._state


def _confirmation_copy(summary: TerminologySyncPlanSummary) -> str | None:
    if not summary.requires_confirmation:
        return None
    counts = dict(summary.counts)
    delete_count = counts.get("delete_remote", 0)
    update_count = counts.get("update_remote", 0)
    return (
        f"将对 {summary.target_identity} 执行远端变更：更新 {update_count} 项、删除 {delete_count} 项。"
        "远端删除不可由 TransBridge 自动撤销，请确认结果后继续。"
    )


__all__ = ["TerminologySyncPresenter", "TerminologySyncViewState"]
