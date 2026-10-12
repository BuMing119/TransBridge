from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.contracts.paratranz.test_sync_execution import NAMESPACE, ControlledRemote, Token, _request
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.publish import CommitDecision
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.sync import ParaTranzSyncExecutor, SyncOperation, SyncPlanner
from transbridge.application.tasks import TaskCleanupFailed


class Transaction:
    def __init__(self, token, failure):
        self.token = token
        self.failure = failure
        self.candidate = ()
        self.official = ()
        self.rolled_back = False

    def stage(self, entries):
        self.candidate = entries
        if self.failure in {"cancel", "rollback"}:
            self.token.cancelled = True
        elif self.failure == "stage":
            raise OSError("staging failure")

    def commit(self, run_id, guard):
        if self.failure == "commit":
            raise OSError("commit failure")
        decision = guard.commit(run_id, lambda: None)
        if decision.accepted:
            self.official = self.candidate
        return decision

    def rollback(self):
        self.rolled_back = True
        if self.failure == "rollback":
            raise OSError("cannot remove staging")
        self.candidate = ()


@pytest.mark.parametrize("failure", ["cancel", "stage", "commit", "rejected", "rollback", None])
def test_local_transaction_rolls_back_only_uncommitted_candidates(failure):
    remote = ControlledRemote((ParaTranzEntry(1, "a", "Original", "Translation", "", 1),))
    token = Token()
    transaction = Transaction(token, failure)
    uow = SimpleNamespace(begin=lambda **_kwargs: transaction)
    executor = ParaTranzSyncExecutor(remote, remote, uow)
    plan = SyncPlanner().plan(
        (),
        remote.fetch(7, NAMESPACE, limit=10),
        operation=SyncOperation.DOWNLOAD,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )
    request = _request(plan, (), cancellation=token, confirmation="CONFIRMED")
    if failure == "rejected":
        request = replace(request, commit_guard=SimpleNamespace(commit=lambda *_args: CommitDecision(False, "stale")))
    if failure == "rollback":
        with pytest.raises(TaskCleanupFailed, match="cannot remove staging") as caught:
            executor.execute(request)
        assert isinstance(caught.value.__cause__, OSError)
        assert transaction.rolled_back and not transaction.official
        return
    result = executor.execute(request)
    if failure is None:
        assert result.outcome is OperationOutcome.COMPLETED
        assert transaction.official and not transaction.rolled_back
    else:
        assert result.outcome is (OperationOutcome.CANCELLED if failure == "cancel" else OperationOutcome.FAILED)
        assert transaction.rolled_back and not transaction.candidate and not transaction.official
