"""Durability, compatibility, and bounded batch work for candidate journals."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Barrier

import pytest

from tests.conftest import make_entry
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.publish import ImmediateCommitGuard
from transbridge.application.translation import (
    FilesystemTranslationCheckpointPort,
    LegacyTranslationCandidateSession,
    candidate_checkpoint_journal as journal,
)
from transbridge.application.translation.candidate_checkpoint import TranslationCheckpoint
from transbridge.application.translation.candidate_checkpoint_store import CandidateCheckpointStore
from transbridge.application.translation.workload_models import CandidateSet, CandidateTranslation
from transbridge.converter.translation_entry_collection import TranslationEntryCollection


def _session(port, **changes):
    return LegacyTranslationCandidateSession(**{
        "run_id": "journal-run",
        "owner_id": "journal-owner",
        "spec_fingerprint": "a" * 64,
        "input_fingerprint": "b" * 64,
        "checkpoint": port,
        "provider": "local",
        "model": "",
        **changes,
    })


def _collection(count=3):
    return TranslationEntryCollection(make_entry(str(index), original=f"Text {index}") for index in range(count))


def _commit(session, collection):
    return session.commit(
        collection,
        RequestContext(
            "journal-owner",
            run_id="journal-run",
            permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
        ),
        ImmediateCommitGuard("journal-run"),
    )


def test_incremental_recovery_replacement_duplicates_and_commit_marks(tmp_path):
    port = FilesystemTranslationCheckpointPort(tmp_path)
    collection = _collection()
    session = _session(port)
    session.accept({"0": "first"}, collection)
    first = port.load("journal-run")
    session.accept({"1": "second"}, collection)
    session.accept({"0": "replacement"}, collection)
    before_duplicate = {path.name: path.read_bytes() for path in tmp_path.glob("*.json*")}
    assert session.accept({"0": "replacement"}, collection).accepted == 0
    assert before_duplicate == {path.name: path.read_bytes() for path in tmp_path.glob("*.json*")}
    assert first.revision == 1 and first.candidates[0].text == "first"
    restored_port = FilesystemTranslationCheckpointPort(tmp_path)
    restored = _session(restored_port)
    assert restored.accept({"1": "second"}, collection).accepted == 0
    result = _commit(restored, collection)
    assert result.outcome is OperationOutcome.COMPLETED
    assert collection.get("0").translation == "replacement"
    assert collection.get("1").translation == "second"
    saved = FilesystemTranslationCheckpointPort(tmp_path).load("journal-run")
    assert saved.revision == 4
    assert result.value["commit_id"] in saved.committed_candidate_ids
    again = _commit(_session(FilesystemTranslationCheckpointPort(tmp_path)), collection)
    assert again.counts.skipped == 2 and not again.value["applied_keys"]


def test_schema_one_is_read_and_migrated_only_after_successful_write(tmp_path, monkeypatch):
    port = FilesystemTranslationCheckpointPort(tmp_path)
    legacy = TranslationCheckpoint("journal-run", "journal-owner", "a" * 64, "b" * 64, revision=7)
    path = port._path(legacy.run_id)
    payload = json.dumps(legacy.to_dict()).encode()
    path.write_bytes(payload)
    session = _session(port)
    assert path.read_bytes() == payload and port.load(legacy.run_id) == legacy
    original = journal.publish_head
    monkeypatch.setattr(journal, "publish_head", lambda *_: (_ for _ in ()).throw(OSError("head unavailable")))
    with pytest.raises(OSError, match="head unavailable"):
        session.accept({"0": "new"}, _collection())
    assert path.read_bytes() == payload
    assert not list(tmp_path.glob("*.jsonl"))
    monkeypatch.setattr(journal, "publish_head", original)
    session.accept({"0": "new"}, _collection())
    assert json.loads(path.read_bytes())["schema_version"] == 2
    assert FilesystemTranslationCheckpointPort(tmp_path).load(legacy.run_id).revision == 8


@pytest.mark.parametrize("failure", ["fsync", "head_fsync", "replace", "verification"])
def test_failed_append_is_not_accepted_and_uncommitted_tail_recovers(tmp_path, monkeypatch, failure):
    port = FilesystemTranslationCheckpointPort(tmp_path)
    collection = _collection()
    session = _session(port)
    session.accept({"0": "durable"}, collection)
    head_before = port._path("journal-run").read_bytes()
    with monkeypatch.context() as fault:
        if failure == "fsync":
            fault.setattr(journal.os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("fsync unavailable")))
        elif failure == "head_fsync":
            original_fsync = journal.os.fsync
            synced = []

            def fail_head_sync(descriptor):
                synced.append(descriptor)
                if len(synced) == 2:
                    raise OSError("head fsync unavailable")
                original_fsync(descriptor)

            fault.setattr(journal.os, "fsync", fail_head_sync)
        elif failure == "replace":
            fault.setattr(journal.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace unavailable")))
        else:
            original = Path.read_bytes
            fault.setattr(Path, "read_bytes", lambda path: b"damaged" if path.suffix == ".tmp" else original(path))
        with pytest.raises((OSError, ValueError), match="unavailable|verification failed"):
            session.accept({"1": "unaccepted"}, collection)
    assert port._path("journal-run").read_bytes() == head_before
    assert all(not entry.translation for entry in collection)
    restored = FilesystemTranslationCheckpointPort(tmp_path).load("journal-run")
    assert restored.revision == 1 and len(restored.candidates) == 1
    session.accept({"1": "retry"}, collection)
    assert _commit(session, collection).counts.succeeded == 2
    assert collection.get("1").translation == "retry"


@pytest.mark.parametrize("location", ["head", "journal", "missing"])
def test_external_corruption_is_detected_before_more_acceptance_or_commit(tmp_path, location):
    port = FilesystemTranslationCheckpointPort(tmp_path)
    collection = _collection()
    session = _session(port)
    session.accept({"0": "durable"}, collection)
    target = port._path("journal-run")
    head = json.loads(target.read_bytes())
    if location == "head":
        head["identity"]["owner_id"] = "another-owner"
        target.write_text(json.dumps(head))
    elif location == "missing":
        target.unlink()
    else:
        path = journal.journal_path(target, head)
        path.write_bytes(path.read_bytes().replace(b"durable", b"altered"))
    with pytest.raises(ValueError):
        session.accept({"1": "new"}, collection)
    with pytest.raises(ValueError):
        _commit(session, collection)
    assert all(not entry.translation for entry in collection)


@pytest.mark.parametrize("field", ["owner_id", "spec_fingerprint", "input_fingerprint"])
def test_identity_mismatch_and_stale_revision_remain_rejected(tmp_path, field):
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    session.accept({"0": "durable"}, _collection())
    current = port.load("journal-run")
    value = "other" if field == "owner_id" else "c" * 64
    with pytest.raises(ValueError, match="identity"):
        port.save(replace(current, revision=2, **{field: value}))
    with pytest.raises(ValueError, match="revision"):
        port.save(current)
    with pytest.raises(ValueError, match="identity"):
        _session(FilesystemTranslationCheckpointPort(tmp_path), **{field: value})


def test_two_ports_cannot_overwrite_the_same_expected_revision(tmp_path):
    collection = _collection()
    ports = [FilesystemTranslationCheckpointPort(tmp_path) for _ in range(2)]
    sessions = [_session(port) for port in ports]
    barrier = Barrier(2)
    for port in ports:
        original = port.save

        def synchronized(update, save=original):
            barrier.wait(timeout=5)
            save(update)

        port.save = synchronized

    def accept(index):
        try:
            sessions[index].accept({str(index): f"candidate {index}"}, collection)
        except ValueError as exc:
            return str(exc)
        return "accepted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, range(2)))
    assert results.count("accepted") == 1
    assert any("revision" in item for item in results)
    restored = FilesystemTranslationCheckpointPort(tmp_path).load("journal-run")
    assert restored.revision == 1 and len(restored.candidates) == 1


def test_compaction_and_batch_serialization_scale_with_new_candidates(tmp_path, monkeypatch):
    count = 300
    collection = _collection(count)
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    encoded = []
    original = CandidateTranslation.to_dict

    def track(candidate):
        encoded.append(candidate.entry_key)
        return original(candidate)

    monkeypatch.setattr(CandidateTranslation, "to_dict", track)
    for index in range(count):
        session.accept({str(index): f"translated {index}"}, collection)
    # Includes write validation and periodic complete snapshots, but no quadratic
    # per-batch serialization of all previously accepted candidates.
    assert len(encoded) < count * 5
    head = json.loads(port._path("journal-run").read_bytes())
    assert head["records"] < count
    assert len(list(tmp_path.glob("*.jsonl"))) == 1
    restored = FilesystemTranslationCheckpointPort(tmp_path).load("journal-run")
    assert restored.revision == count and len(restored.candidates) == count


def test_compaction_failure_preserves_previous_committed_generation(tmp_path, monkeypatch):
    monkeypatch.setattr(CandidateCheckpointStore, "COMPACT_MIN_RECORDS", 2)
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    collection = _collection(10)
    session.accept({"0": "first"}, collection)
    session.accept({"1": "second" * 1000}, collection)
    before = port.load("journal-run")
    paths_before = set(tmp_path.glob("*.jsonl"))
    with monkeypatch.context() as fault:
        fault.setattr(journal, "publish_head", lambda *_: (_ for _ in ()).throw(OSError("compact unavailable")))
        with pytest.raises(OSError, match="compact unavailable"):
            session.accept({"2": "third"}, collection)
    assert set(tmp_path.glob("*.jsonl")) == paths_before
    assert FilesystemTranslationCheckpointPort(tmp_path).load("journal-run") == before
    session.accept({"2": "third"}, collection)
    assert len(list(tmp_path.glob("*.jsonl"))) == 1
    assert FilesystemTranslationCheckpointPort(tmp_path).load("journal-run").revision == 3


def test_alias_lookup_uses_exact_keys_and_invalidates_on_collection_changes(tmp_path, monkeypatch):
    collection = _collection()
    session = _session(FilesystemTranslationCheckpointPort(tmp_path))

    def forbid_scan(*args, **kwargs):
        raise AssertionError("candidate acceptance must not scan the collection for each response id")

    monkeypatch.setattr(collection, "_legacy_matches", forbid_scan)
    session.accept({"0": "first", "1": "second"}, collection)
    third = next(entry for entry in collection if entry.key == "2")
    # Exact remove does not need the compatibility scanning facade.
    collection.remove(third.identity)
    assert session.accept({"2": "removed"}, collection).accepted == 0
    snapshot = session._checkpoint.load("journal-run")
    assert len(snapshot.candidates) == 2


def test_unversioned_mutable_provider_rebuilds_aliases_and_noniterable_facade_still_works(tmp_path):
    class MutableProvider:
        def __init__(self):
            self.collection = _collection(1)

        def __iter__(self):
            return iter(self.collection)

        def get(self, key):
            return self.collection.get(key)

    provider = MutableProvider()
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    session.accept({"0": "first"}, provider)
    provider.collection = TranslationEntryCollection([make_entry("different", original="Changed")])
    assert session.accept({"different": "second"}, provider).accepted == 1

    class LegacyFacade:
        def get(self, key):
            return provider.collection.get(key)

        def get_by_id(self, key):
            return provider.collection.get_by_id(key)

    assert session.accept({"different": "third"}, LegacyFacade()).accepted == 1


def test_alias_lookup_preserves_local_priority_and_ambiguous_identity_rejection(tmp_path):
    def entry(namespace, local, identifier):
        return replace(make_entry(identifier), key=local, entry_key=EntryKey(SourceNamespace(namespace), local))

    prioritized = entry("first", "priority", "first-id")
    shadowed = entry("second", "other", "priority")
    ambiguous = [entry("third", "same", "shared-id"), entry("fourth", "same", "shared-id")]
    collection = TranslationEntryCollection([prioritized, shadowed, *ambiguous])
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    with pytest.warns(RuntimeWarning, match="ambiguous"):
        assert session.accept({"same": "rejected", "shared-id": "rejected"}, collection).accepted == 0
    assert (
        session.accept({"priority": "selected", ambiguous[0].identity.serialize(): "exact"}, collection).accepted == 2
    )
    saved = {item.entry_key: item.text for item in port.load("journal-run").candidates}
    assert saved == {prioritized.identity: "selected", ambiguous[0].identity: "exact"}


def test_repeated_response_aliases_keep_the_existing_last_candidate_wins_behavior(tmp_path):
    collection = _collection(1)
    entry = next(iter(collection))
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    accepted = session.accept({entry.key: "first", entry.identity.serialize(): "last"}, collection)
    assert accepted.accepted == 2
    saved = port.load("journal-run")
    assert len(saved.candidates) == 1 and saved.candidates[0].text == "last"


def test_commit_synchronizes_session_revision_and_evidence_failure_keeps_partial_result(tmp_path, monkeypatch):
    port = FilesystemTranslationCheckpointPort(tmp_path)
    collection = _collection()
    session = _session(port)
    translations = {"0": "first"}
    session.accept(translations, collection)
    assert _commit(session, collection).outcome is OperationOutcome.COMPLETED
    assert session.accept(translations, collection).accepted == 0
    session.accept({"1": "second"}, collection)
    original = port.save

    def fail_commit(checkpoint):
        if isinstance(checkpoint, TranslationCheckpoint):
            raise OSError("commit evidence unavailable")
        original(checkpoint)

    monkeypatch.setattr(port, "save", fail_commit)
    result = _commit(session, collection)
    assert result.outcome is OperationOutcome.PARTIAL
    assert any(item.code == "TRANSLATION_COMMIT_EVIDENCE_FAILED" for item in result.diagnostics)
    assert collection.get("1").translation == "second"


def test_unchanged_revision_external_snapshot_rewrite_is_rejected(tmp_path):
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    session.accept({"0": "first"}, _collection())
    current = port.load("journal-run")
    port._path("journal-run").write_text(json.dumps(current.to_dict()))
    with pytest.raises(ValueError, match="outside this port"):
        session.accept({"0": "first"}, _collection())


def test_compaction_published_head_survives_interruption_before_old_generation_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(CandidateCheckpointStore, "COMPACT_MIN_RECORDS", 2)
    port = FilesystemTranslationCheckpointPort(tmp_path)
    session = _session(port)
    collection = _collection()
    session.accept({"0": "first"}, collection)
    session.accept({"1": "second" * 1000}, collection)
    old = next(tmp_path.glob("*.jsonl"))
    original = Path.unlink

    def interrupt_cleanup(path, **kwargs):
        if path == old:
            raise SystemExit("process interrupted")
        return original(path, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "unlink", interrupt_cleanup)
        with pytest.raises(SystemExit, match="interrupted"):
            session.accept({"2": "third"}, collection)
    recovered = FilesystemTranslationCheckpointPort(tmp_path).load("journal-run")
    assert recovered.revision == 3 and len(recovered.candidates) == 3
    assert len(list(tmp_path.glob("*.jsonl"))) == 2


def test_large_repeated_commit_hashes_one_fixed_candidate_set(tmp_path, monkeypatch):
    count = 500
    port = FilesystemTranslationCheckpointPort(tmp_path)
    collection = _collection(count)
    session = _session(port)
    session.accept({str(index): f"translation {index}" for index in range(count)}, collection)
    assert _commit(session, collection).counts.succeeded == count
    before = tuple(entry.snapshot() for entry in collection)
    hashed = []
    original = CandidateSet.to_dict

    def record(candidate_set):
        hashed.append(len(candidate_set.candidates))
        return original(candidate_set)

    monkeypatch.setattr(CandidateSet, "to_dict", record)
    result = _commit(_session(FilesystemTranslationCheckpointPort(tmp_path)), collection)
    assert result.outcome is OperationOutcome.COMPLETED and result.counts.skipped == count
    assert tuple(entry.snapshot() for entry in collection) == before
    assert hashed == [count]
