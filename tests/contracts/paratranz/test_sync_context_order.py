from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.contracts.paratranz.test_sync_execution import NAMESPACE, ControlledRemote, _executor, _local, _request
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.paratranz_context_order import parse_ordered_context
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.sync import ConflictPolicy, RetryToken, SyncOperation, SyncPlanner
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.tool_paratranz import _local_sync_snapshots
from transbridge.ui.operations.production_support import local_snapshots


class OrderedRemote(ControlledRemote):
    def fetch(self, *args, **kwargs):
        snapshots = super().fetch(*args, **kwargs)
        return tuple(
            replace(
                snapshot,
                context=parse_ordered_context(snapshot.context)[0],
                context_order=parse_ordered_context(snapshot.context)[1],
            )
            for snapshot in snapshots
        )


@pytest.mark.parametrize("local_order,expected", [(None, 428), (123, 123)])
def test_update_remote_retains_or_refreshes_order(local_order, expected):
    remote = OrderedRemote((ParaTranzEntry(71, "a", "original-a", "old", "00000428|BOOK:FULL", 1),))
    local = (replace(_local("a", "new"), context="BOOK:FULL", context_order=local_order),)
    plan = SyncPlanner().plan(
        local,
        remote.fetch(7, NAMESPACE, limit=10),
        operation=SyncOperation.UPLOAD,
        conflict_policy=ConflictPolicy.PREFER_LOCAL,
        scope=f"paratranz:project:7:source:{NAMESPACE.value}",
    )
    result = _executor(remote, list(local)).execute(_request(plan, local, confirmation="CONFIRMED"))
    assert result.outcome is OperationOutcome.COMPLETED
    assert remote.entries["a"].context == f"{expected:08d}|BOOK:FULL"


@pytest.mark.parametrize(
    "context,expected", [("BOOK:FULL", "00000428|BOOK:FULL"), ("arbitrary prose", "arbitrary prose")]
)
def test_create_remote_formats_only_structured_context(context, expected):
    remote = OrderedRemote()
    local = (replace(_local("a", "new"), context=context, context_order=428),)
    plan = SyncPlanner().plan(
        local, (), operation=SyncOperation.UPLOAD, scope=f"paratranz:project:7:source:{NAMESPACE.value}"
    )
    result = _executor(remote, list(local)).execute(_request(plan, local))
    assert result.outcome is OperationOutcome.COMPLETED
    assert remote.entries["a"].context == expected


def test_local_snapshot_order_uses_complete_collection_before_selection():
    entries = [
        TranslationEntry(id=key, key=key, original=key, translation="", stage=0, context="BOOK:FULL")
        for key in ("a", "b", "c")
    ]
    collection = TranslationEntryCollection(entries)
    snapshots = local_snapshots(SimpleNamespace(collection=collection), 7)
    assert [item.context_order for item in snapshots] == [0, 1, 2]
    _, selected = _local_sync_snapshots(collection, 7, ["c"])
    assert selected[0].context_order == 2


def test_order_change_invalidates_snapshot_hash():
    local = replace(_local("a", "new"), context="BOOK:FULL", context_order=428)
    assert SyncPlanner().snapshot_hashes((local,), ()) != SyncPlanner().snapshot_hashes(
        (replace(local, context_order=429),), ()
    )


def test_retry_rejects_order_change_on_unfinished_item():
    local = [
        replace(_local(key, key), context="BOOK:FULL", context_order=index) for index, key in enumerate(("a", "b"))
    ]
    remote = OrderedRemote()
    remote.fail_keys.add("b")
    plan = SyncPlanner().plan(
        local, (), operation=SyncOperation.UPLOAD, scope=f"paratranz:project:7:source:{NAMESPACE.value}"
    )
    first = _executor(remote, local).execute(_request(plan, local))
    assert first.outcome is OperationOutcome.PARTIAL
    retry = RetryToken.from_dict(first.value["retry_token"])
    remote.fail_keys.clear()
    local[1] = replace(local[1], context_order=99)
    second = _executor(remote, local).execute(_request(plan, local, token=retry))
    assert second.outcome is OperationOutcome.FAILED
    assert "b" not in remote.entries
