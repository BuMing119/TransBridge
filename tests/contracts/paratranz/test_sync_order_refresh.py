from dataclasses import replace

import pytest

from tests.contracts.paratranz.test_sync_context_order import OrderedRemote
from tests.contracts.paratranz.test_sync_execution import NAMESPACE, _executor, _local, _request
from transbridge.application.contracts import OperationOutcome
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError, ParaTranzEntry
from transbridge.application.sync import RetryToken, SyncOperation, SyncPlanner
from transbridge.application.sync.models import SyncAction


class ContextRemote(OrderedRemote):
    def update_entry_context(self, project_id, entry, *, cancellation=None):
        self.calls.append(("context", entry.key))
        if entry.key in self.fail_keys:
            raise ExternalServiceError(ExternalServiceCategory.RATE_LIMITED, "try again", status=429)
        existing = self.entries[entry.key]
        assert (entry.original, entry.translation, entry.stage, entry.remote_id) == (
            existing.original,
            existing.translation,
            existing.stage,
            existing.remote_id,
        )
        self.entries[entry.key] = replace(existing, context=entry.context)
        return self.entries[entry.key]


def _setup(*, context="BOOK:FULL", remote_order=None, local_order=428):
    local = replace(_local("a", "Translated"), context=context, context_order=local_order)
    wire_context = context if remote_order is None else f"{remote_order:08d}|{context}"
    remote = ContextRemote((ParaTranzEntry(71, "a", local.original, local.translation, wire_context, local.stage),))
    return local, remote


def _plan(local, remote, operation=SyncOperation.UPLOAD, *, local_state_only=False):
    return SyncPlanner(local_state_only=local_state_only).plan(
        local,
        remote.fetch(7, NAMESPACE, limit=10),
        operation=operation,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )


@pytest.mark.parametrize("operation", [SyncOperation.UPLOAD, SyncOperation.BIDIRECTIONAL])
@pytest.mark.parametrize("old_order", [None, 123])
def test_order_only_plan_refreshes_context_with_confirmation(operation, old_order):
    local, remote = _setup(remote_order=old_order)
    plan = _plan((local,), remote, operation)
    assert plan.items[0].action is SyncAction.UPDATE_REMOTE
    assert plan.items[0].reason == "context_order_changed"
    assert plan.requires_confirmation
    executor = _executor(remote, [local])
    rejected = executor.execute(_request(plan, (local,)))
    assert rejected.outcome is OperationOutcome.FAILED
    assert remote.calls == []
    result = executor.execute(_request(plan, (local,), confirmation="CONFIRMED"))
    assert result.outcome is OperationOutcome.COMPLETED
    assert remote.calls == [("context", "a")]
    assert remote.entries["a"].context == "00000428|BOOK:FULL"
    assert _plan((local,), remote, operation).items[0].action is SyncAction.SKIP


@pytest.mark.parametrize("kwargs", [{"remote_order": 428}, {"local_order": None}, {"context": "free text"}])
def test_same_order_missing_local_order_or_free_text_skips(kwargs):
    local, remote = _setup(**kwargs)
    assert _plan((local,), remote).items[0].action is SyncAction.SKIP


def test_download_does_not_refresh_remote_order():
    local, remote = _setup()
    plan = _plan((local,), remote, SyncOperation.DOWNLOAD)
    assert plan.items[0].action is SyncAction.SKIP
    assert _executor(remote, [local]).execute(_request(plan, (local,))).outcome is OperationOutcome.COMPLETED
    assert remote.calls == []


def test_refresh_requires_remote_identity():
    local, remote = _setup()
    snapshot = replace(remote.fetch(7, NAMESPACE, limit=10)[0], external_ref=None)
    plan = SyncPlanner().plan((local,), (snapshot,), operation=SyncOperation.UPLOAD)
    assert plan.items[0].action is SyncAction.CONFLICT
    assert plan.items[0].reason == "remote_id_missing"


def test_different_source_content_never_uses_order_only_update():
    local, remote = _setup()
    local = replace(local, original="Different original")
    plan = _plan((local,), remote, local_state_only=True)
    assert plan.items[0].action is SyncAction.CONFLICT
    assert plan.items[0].reason == "content_changed"


def test_changed_remote_translation_rejects_order_refresh_plan():
    local, remote = _setup()
    plan = _plan((local,), remote)
    remote.entries["a"] = replace(remote.entries["a"], translation="New remote translation")
    result = _executor(remote, [local]).execute(_request(plan, (local,), confirmation="CONFIRMED"))
    assert result.outcome is OperationOutcome.FAILED
    assert remote.calls == []
    assert remote.entries["a"].translation == "New remote translation"


def test_partial_order_refresh_retry_does_not_repeat_completed_patch():
    a, remote = _setup()
    b = replace(a, entry_key=replace(a.entry_key, local_key="b"), context_order=429)
    remote.entries["b"] = replace(remote.entries["a"], remote_id=72, key="b")
    local = (a, b)
    plan = _plan(local, remote)
    remote.fail_keys.add("b")
    first = _executor(remote, list(local)).execute(_request(plan, local, confirmation="CONFIRMED"))
    assert first.outcome is OperationOutcome.PARTIAL
    remote.fail_keys.clear()
    token = RetryToken.from_dict(first.value["retry_token"])
    second = _executor(remote, list(local)).execute(_request(plan, local, token=token, confirmation="CONFIRMED"))
    assert second.outcome is OperationOutcome.COMPLETED
    assert remote.calls == [("context", "a"), ("context", "b"), ("context", "b")]
    assert remote.entries["a"].context == "00000428|BOOK:FULL"
    assert remote.entries["b"].context == "00000429|BOOK:FULL"
