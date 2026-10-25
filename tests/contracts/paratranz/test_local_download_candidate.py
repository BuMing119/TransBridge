"""Local candidate flags separate download content from durable sync identity."""

from dataclasses import replace

import pytest

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.sync.local_candidate import RemoteSourceIndex, build_local_candidate
from transbridge.application.sync.models import (
    ConflictPolicy,
    EntrySummary,
    LocalEntrySnapshot,
    RemoteEntrySnapshot,
    SyncAction,
    SyncPlanItem,
)

NS = SourceNamespace("download-candidate")


def _sources():
    local = LocalEntrySnapshot(
        EntryKey(NS, "key"),
        EntryRevision(4),
        "Local original",
        "Local translation",
        "Local context",
        1,
        ExternalEntryRef("paratranz", "project:7", 99),
        context_order=7,
    )
    remote = RemoteEntrySnapshot(
        local.entry_key,
        "revision",
        "Remote original",
        "Remote translation",
        "Remote context",
        5,
        ExternalEntryRef("paratranz", "project:7", 1),
        context_order=42,
    )
    item = SyncPlanItem(
        local.entry_key,
        SyncAction.UPDATE_LOCAL,
        EntrySummary.from_local(local),
        EntrySummary.from_remote(remote),
        remote.external_ref,
        "remote_preferred",
        ConflictPolicy.PREFER_REMOTE,
    )
    return local, remote, item


@pytest.mark.parametrize("attach", [False, True])
@pytest.mark.parametrize("preserve", [False, True])
def test_download_candidate_flags_control_source_layout_and_remote_association_independently(attach, preserve):
    local, remote, item = _sources()
    candidate = build_local_candidate(
        (local,), RemoteSourceIndex((remote,)), (item,), attach_remote_refs=attach, preserve_local_source=preserve
    )[0]
    assert candidate.entry_key == local.entry_key
    assert candidate.revision == local.revision.next()
    assert (candidate.translation, candidate.stage) == (remote.translation, remote.stage)
    source = local if preserve else remote
    assert (candidate.original, candidate.context, candidate.context_order) == (
        source.original,
        source.context,
        source.context_order,
    )
    assert candidate.external_ref == (remote.external_ref if attach else None)
    assert local.external_ref.opaque_id == 99


def test_default_candidate_retains_bidirectional_sync_behavior():
    local, remote, item = _sources()
    candidate = build_local_candidate((local,), RemoteSourceIndex((remote,)), (item,))[0]
    assert (candidate.original, candidate.context, candidate.context_order, candidate.external_ref) == (
        remote.original,
        remote.context,
        remote.context_order,
        remote.external_ref,
    )


def test_new_download_entry_can_copy_reviewed_content_without_creating_an_association():
    _local, remote, item = _sources()
    item = replace(item, action=SyncAction.CREATE_LOCAL, before=None)
    candidate = build_local_candidate(
        (), RemoteSourceIndex((remote,)), (item,), attach_remote_refs=False, preserve_local_source=True
    )[0]
    assert candidate.original == remote.original
    assert candidate.translation == remote.translation
    assert candidate.external_ref is None
