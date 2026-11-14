from dataclasses import replace

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.stage_policy import Stage
from transbridge.persistence.v2.deferred_variant_rows import DeferredVariantRows
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.translation_epochs import publish_alignment_epochs
from transbridge.persistence.v2.variant import (
    SourceFingerprint,
    VariantAggregate,
    VariantChangeSet,
    VariantEntryState,
    VariantSnapshot,
)


def _aggregate(*, deferred=False):
    rows = tuple(VariantEntryState(EntryKey(SourceNamespace("source"), str(i)), translation="A") for i in range(3))
    reads = []

    def full_range(*_args):
        raise AssertionError("A sparse edit must not enumerate the Variant or cache")

    def read_entries(keys):
        reads.append(tuple(keys))
        return tuple(row for row in rows if row.entry_key in keys)

    entries = DeferredVariantRows(full_range, read_entries, None) if deferred else rows
    ref = VariantRef(VariantId("main"), ProjectId("project"))
    return (
        VariantAggregate(VariantSnapshot(ref, (SourceFingerprint(SourceNamespace("source"), None),), entries)),
        rows,
        reads,
    )


def _commit(aggregate, changed, *, before_commit=None):
    snapshot = aggregate.snapshot()
    deferred = isinstance(snapshot.entries, DeferredVariantRows)
    entries = (
        snapshot.entries.patched(changed)
        if deferred
        else tuple({row.entry_key: row for row in (*snapshot.entries, *changed)}.values())
    )
    return aggregate.commit(
        VariantChangeSet(
            aggregate.ref,
            snapshot.revision,
            snapshot.source_fingerprints,
            entries,
            (),
            "edit",
            sparse_entries=tuple(changed) if deferred else (),
        ),
        RequestContext(owner_id="test", run_id="edit"),
        before_commit=before_commit,
    )


@pytest.mark.parametrize("deferred", [False, True])
def test_translation_changes_invalidate_only_changed_entry_and_cannot_revive_after_undo(deferred):
    aggregate, rows, _reads = _aggregate(deferred=deferred)
    epochs = aggregate.alignment_epochs
    events = []
    unsubscribe = epochs.subscribe(events.append)
    _commit(aggregate, (replace(rows[0], translation="B"),))
    first = epochs.stamp(rows[0].entry_key)
    assert first is not None
    assert epochs.stamp(rows[1].entry_key) is None
    _commit(aggregate, (rows[0],))
    assert epochs.stamp(rows[0].entry_key) is not first
    assert events == [(rows[0].entry_key,), (rows[0].entry_key,)]
    assert epochs.changed_keys() == (rows[0].entry_key,)
    unsubscribe()
    _commit(aggregate, (replace(rows[0], translation="C"),))
    assert len(events) == 2


@pytest.mark.parametrize("deferred", [False, True])
def test_metadata_and_identical_translation_do_not_invalidate(deferred):
    aggregate, rows, _reads = _aggregate(deferred=deferred)
    _commit(aggregate, (replace(rows[0], stage=Stage.REVIEWED, labels=("review",)),))
    _commit(aggregate, (replace(rows[0], stage=Stage.TRANSLATED),))
    assert aggregate.alignment_epochs.changed_keys() == ()
    _commit(aggregate, (replace(rows[0], tombstone=True),))
    assert aggregate.alignment_epochs.stamp(rows[0].entry_key) is not None


def test_sparse_edit_reads_only_its_keys_and_not_complete_variant():
    aggregate, rows, reads = _aggregate(deferred=True)
    _commit(aggregate, (replace(rows[1], translation="B"),))
    assert reads == [(rows[1].entry_key,)]


def test_resident_commit_reuses_its_existing_entry_comparison(monkeypatch):
    from transbridge.persistence.v2 import variant

    aggregate, rows, _reads = _aggregate()

    def unexpected_scan(*_args, **_kwargs):
        raise AssertionError("Resident edits must not add a second full alignment comparison")

    monkeypatch.setattr(variant, "changed_translation_keys", unexpected_scan)
    _commit(aggregate, (replace(rows[0], translation="B"),))
    assert aggregate.alignment_epochs.stamp(rows[0].entry_key) is not None


def test_fork_isolation_and_acknowledgement_keep_existing_generations():
    aggregate, rows, _reads = _aggregate()
    _commit(aggregate, (replace(rows[0], translation="B"),))
    first = aggregate.alignment_epochs.stamp(rows[0].entry_key)
    events = []
    aggregate.alignment_epochs.subscribe(events.append)
    fork = aggregate.fork()
    assert fork.alignment_epochs.stamp(rows[0].entry_key) is first
    _commit(fork, (replace(rows[0], translation="C"),))
    assert events == []
    assert aggregate.alignment_epochs.stamp(rows[0].entry_key) is first
    assert fork.alignment_epochs.stamp(rows[0].entry_key) is not first
    aggregate.acknowledge_changes(1, 1, saved_snapshot=aggregate.snapshot())
    assert aggregate.alignment_epochs.stamp(rows[0].entry_key) is first


def test_rejected_commit_does_not_invalidate_or_notify_and_accepted_callback_observes_new_text():
    aggregate, rows, _reads = _aggregate()
    seen = []
    aggregate.alignment_epochs.subscribe(lambda _keys: seen.append(aggregate.snapshot().entries[0].translation))

    def reject(_change):
        raise ValueError("rejected")

    with pytest.raises(ValueError, match="rejected"):
        _commit(aggregate, (replace(rows[0], translation="B"),), before_commit=reject)
    assert aggregate.alignment_epochs.changed_keys() == ()
    assert seen == []
    _commit(aggregate, (replace(rows[0], translation="B"),))
    assert seen == ["B"]


def test_published_replacement_authority_inherits_only_within_same_variant():
    aggregate, rows, _reads = _aggregate()
    _commit(aggregate, (replace(rows[0], translation="B"),))
    first = aggregate.alignment_epochs.stamp(rows[0].entry_key)
    same = VariantAggregate(replace(aggregate.snapshot(), entries=(replace(rows[0], translation="C"), *rows[1:])))
    publish_alignment_epochs(aggregate, same)
    assert same.alignment_epochs is aggregate.alignment_epochs
    assert same.alignment_epochs.stamp(rows[0].entry_key) is not first
    assert same.alignment_epochs.stamp(rows[1].entry_key) is None
    other = VariantAggregate(replace(same.snapshot(), ref=VariantRef(VariantId("other"), ProjectId("project"))))
    publish_alignment_epochs(same, other)
    assert other.alignment_epochs.changed_keys() == ()


def test_failed_observer_does_not_misreport_an_accepted_edit(caplog):
    aggregate, rows, _reads = _aggregate()

    def broken(_keys):
        raise RuntimeError("failed observer")

    aggregate.alignment_epochs.subscribe(broken)
    _commit(aggregate, (replace(rows[0], translation="B"),))
    assert aggregate.snapshot().entries[0].translation == "B"
    assert aggregate.alignment_epochs.stamp(rows[0].entry_key) is not None
    assert "failed observer" in caplog.text
