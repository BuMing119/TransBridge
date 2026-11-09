"""Reconcile task decisions against project commits without replaying writes."""

from __future__ import annotations

from dataclasses import replace
import logging
from uuid import uuid4

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.result_merge import ResultMergePlan

from .storage_contracts import ApplicationState, ApplyCandidate, ApplyRequest

_logger = logging.getLogger(__name__)


class ResultApplication:
    def __init__(self, decisions, receipt_reader, target_exists, *, is_staged=None):
        self.decisions = decisions
        self._receipt_reader = receipt_reader
        self._target_exists = target_exists
        self._is_staged = is_staged or (lambda _operation_id: False)

    @classmethod
    def for_commands(cls, recovery, commands):
        if recovery is None or not hasattr(recovery, "decisions"):
            return None
        return cls(
            recovery.decisions,
            commands.result_receipt,
            commands.project_exists,
            is_staged=getattr(commands, "result_is_staged", None),
        )

    def prepare(self, run_id, project_id, variant_id, rows, policy, *, decision="merge", operation_id=None):
        request = ApplyRequest(
            operation_id if operation_id is not None else uuid4().hex,
            run_id,
            str(project_id),
            str(variant_id),
            tuple(ApplyCandidate(EntryKey.from_dict(row["entry_key"]), row["candidate_revision"]) for row in rows),
            str(policy),
            decision,
        )
        application = (
            self.decisions.find_or_prepare(request) if operation_id is None else self.decisions.prepare(request)
        )
        application = self.reconcile(application.request.operation_id)
        if application.state is ApplicationState.TARGET_UNAVAILABLE:
            raise ValueError("原项目已删除，任务成果仍可查看和导出。")
        return application

    def reconcile(self, operation_id):
        application = self.decisions.get(operation_id)
        if application.receipt is not None:
            return application
        request = application.request
        if not self._target_exists(request.project_id):
            return self.decisions.set_state(operation_id, ApplicationState.TARGET_UNAVAILABLE)
        receipt = self._receipt_reader(request.project_id, operation_id)
        if receipt is not None:
            return self.acknowledge(application, receipt)
        if self._is_staged(operation_id):
            return self.decisions.set_state(operation_id, ApplicationState.STAGED_UNSAVED)
        if application.state in {ApplicationState.STAGED_UNSAVED, ApplicationState.ACKNOWLEDGEMENT_PENDING}:
            return self.decisions.set_state(operation_id, ApplicationState.DISCARDED)
        return application

    def acknowledge(self, application, receipt):
        request = application.request
        if (
            receipt.operation_id != request.operation_id
            or str(receipt.project_id) != request.project_id
            or str(receipt.variant_id) != request.variant_id
        ):
            raise ValueError("Project receipt does not match the task application identity")
        expected = self.decisions.candidate_values(request)
        outcomes = getattr(receipt, "application_outcomes", ())
        if outcomes:
            confirmed = [
                (key, translation, stage)
                for key, decision, translation, stage in outcomes
                if decision in {"applied", "unchanged"}
                and key in expected
                and (request.decision == "restore" or expected.get(key) == (translation, stage))
            ]
        else:
            confirmed = [
                (state.entry_key, state.translation, state.stage.value)
                for state in receipt.entries
                if expected.get(state.entry_key) == (state.translation, state.stage.value)
            ]
        value = {
            "project_revision": receipt.project_revision,
            "variant_revision": receipt.variant_revision,
            "entries": [
                {"entry_key": key.to_dict(), "translation": translation, "stage": stage}
                for key, translation, stage in confirmed
            ],
            "dispositions": [{"entry_key": key.to_dict(), "state": decision} for key, decision, _, _ in outcomes],
        }
        state = (
            ApplicationState.COMMITTED
            if confirmed
            else (
                ApplicationState.CONFLICTED
                if any(decision == "conflicted" for _, decision, _, _ in outcomes)
                else ApplicationState.DISCARDED
            )
        )
        try:
            return self.decisions.acknowledge(request.operation_id, value, state)
        except (OSError, ValueError) as exc:
            _logger.warning(
                "Project operation %s committed but task acknowledgement failed: %s", request.operation_id, exc
            )
            return replace(application, state=ApplicationState.ACKNOWLEDGEMENT_PENDING, receipt=value)

    def list(self, run_id):
        return tuple(self.reconcile(item.request.operation_id) for item in self.decisions.list(run_id))

    def finish(self, application, plan):
        operation_id = application.request.operation_id
        try:
            latest = self.reconcile(operation_id)
            if latest.receipt is None and latest.state is ApplicationState.PREPARED:
                state = (
                    ApplicationState.STAGED_UNSAVED
                    if plan.applied or plan.unchanged_keys
                    else ApplicationState.CONFLICTED
                )
                return self.decisions.set_state(operation_id, state)
            return latest
        except (OSError, ValueError) as exc:
            _logger.warning("Cannot update task application %s after project mutation: %s", operation_id, exc)
            return replace(application, state=ApplicationState.ACKNOWLEDGEMENT_PENDING)


def select_saved_rows(store, run_id, candidates):
    saved = {EntryKey.from_dict(row["entry_key"]): row for row in (store.load_results(run_id) or {}).get("rows", ())}
    rows = []
    for candidate in candidates:
        proposed = candidate.incoming
        row = saved.get(proposed.entry_key)
        if row is None or (row["text"], row["stage"]) != (proposed.translation, proposed.stage):
            raise ValueError("选中的成果修订尚未可靠保存，请重试保存成果后再合入。")
        rows.append(row)
    return tuple(rows)


def prior_merge_plan(application):
    if application.state is ApplicationState.STAGED_UNSAVED:
        return ResultMergePlan()
    if application.receipt is None:
        return None
    return ResultMergePlan(
        unchanged_keys=tuple(EntryKey.from_dict(row["entry_key"]) for row in application.receipt["entries"])
    )
