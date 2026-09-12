from dataclasses import replace
import hashlib
from threading import Event, Thread

import pytest

from transbridge.application.assistant_requests.transcript import TranscriptManifest, TranscriptMessage
from transbridge.persistence.assistant_attachment_cleanup import AssistantAttachmentCleanup
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.ids import EntityKind, SessionId, SessionRef
from transbridge.persistence.v2.models import (
    BackupVerificationError,
    PathBoundaryError,
    ReadOnlyWriteRefused,
    SchemaEnvelope,
    SchemaValidationError,
    SessionDto,
)
from transbridge.persistence.v2.repository import SessionRepository
from transbridge.persistence.v2.schema import serialize_document
from transbridge.persistence.v2.session_write_lease import session_write_lease


def _document(sid, manifest, **values):
    return {
        "schema_version": 4,
        "entity_type": "session",
        "id": sid,
        "revision": 0,
        "data": {
            "name": "Session",
            "messages": [],
            "project_id": None,
            "variant_id": None,
            "transcript_manifest": manifest.to_dict(),
            **values,
        },
    }


def _save(root, sid, manifest, **values):
    document = _document(sid, manifest, **values)
    repository = SessionRepository(str(root), OsPersistenceFilesystem())
    ref = SessionRef(SessionId(sid))
    repository.save(ref, SessionDto(SchemaEnvelope(4, EntityKind.SESSION, sid, 0, document["data"])))
    return repository, ref


def _write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def test_cleanup_preserves_current_backup_and_retained_references_without_age_rules(tmp_path):
    store = AssistantTranscriptStore(str(tmp_path))
    refs = [
        store.write_artifact("s", value.encode())
        for value in ("current", "backup", "retained-file", "retained-api", "orphan")
    ]
    repository, ref = _save(tmp_path, "s", TranscriptManifest(artifacts=(refs[0],)))
    backup = serialize_document(_document("s", TranscriptManifest(artifacts=(refs[1],))))
    _write(
        tmp_path / "backups" / "sessions" / SessionId("s").encoded / f"{hashlib.sha256(backup).hexdigest()}.v4.json",
        backup,
    )
    _write(
        tmp_path / "retained" / "sessions" / "retained.json",
        serialize_document(_document("s", TranscriptManifest(artifacts=(refs[2],)))),
    )
    orphan_segment = store.append(
        "s", TranscriptManifest(), (TranscriptMessage("m", 1, "user", "unpublished"),)
    ).segments[0]
    cleanup = AssistantAttachmentCleanup(tmp_path)
    preview = cleanup.collect(retained_artifacts=(("s", refs[3]),))
    assert set(preview.candidates) == {refs[4].path, orphan_segment.path}
    assert not preview.removed
    assert all((tmp_path / value.path).exists() for value in refs)
    result = cleanup.collect(dry_run=False, quiescent=True, retained_artifacts=(("s", refs[3]),))
    assert set(result.removed) == set(preview.candidates)
    assert all((tmp_path / value.path).exists() for value in refs[:4])
    assert repository.load(ref).value.envelope.identity == "s"
    store.validate("s", TranscriptManifest(artifacts=(refs[1],)))


def test_nested_typed_reference_is_retained_and_an_unmanaged_text_file_is_never_removed(tmp_path):
    store = AssistantTranscriptStore(str(tmp_path))
    child = store.write_artifact("s", b"child evidence")
    parent = store.write_artifact("s", serialize_document({"proof": child.to_dict()}))
    orphan = store.write_artifact("s", b"unused")
    _save(tmp_path, "s", TranscriptManifest(artifacts=(parent,)))
    note = tmp_path / "assistant" / "user-note.txt"
    note.write_text("not an owned attachment")
    result = AssistantAttachmentCleanup(tmp_path).collect(dry_run=False, quiescent=True)
    assert result.removed == (orphan.path,)
    assert child.path in result.referenced
    assert note.read_text() == "not an owned attachment"


def test_apply_requires_explicit_offline_assertion_and_preview_is_not_authority(tmp_path):
    store = AssistantTranscriptStore(str(tmp_path))
    reference = store.write_artifact("s", b"not yet committed")
    cleanup = AssistantAttachmentCleanup(tmp_path)
    assert cleanup.collect().candidates == (reference.path,)
    with pytest.raises(ValueError, match="offline"):
        cleanup.collect(dry_run=False)
    _save(tmp_path, "s", TranscriptManifest(artifacts=(reference,)))
    assert not cleanup.collect(dry_run=False, quiescent=True).removed
    assert (tmp_path / reference.path).exists()


@pytest.mark.parametrize(
    "damage",
    [
        "current-json",
        "future-session",
        "backup-hash",
        "unknown-manifest",
        "staging",
        "corrupt-orphan",
        "future-artifact",
        "unknown-layout",
    ],
)
def test_unknown_or_corrupt_source_aborts_before_deleting_any_attachment(tmp_path, damage):
    store = AssistantTranscriptStore(str(tmp_path))
    orphan = store.write_artifact("s", b"unused")
    repository, ref = _save(tmp_path, "s", TranscriptManifest())
    current = tmp_path / "sessions" / f"{SessionId('s').encoded}.json"
    document = _document("s", TranscriptManifest())
    if damage == "current-json":
        current.write_bytes(b"not-json")
    elif damage == "future-session":
        document["schema_version"] = 99
        current.write_bytes(serialize_document(document))
    elif damage == "backup-hash":
        _write(
            tmp_path / "backups" / "sessions" / SessionId("s").encoded / f"{'0' * 64}.v4.json",
            serialize_document(document),
        )
    elif damage == "unknown-manifest":
        document["data"]["transcript_manifest"]["future_refs"] = []
        current.write_bytes(serialize_document(document))
    elif damage == "staging":
        _write(tmp_path / ".staging" / "pending.tmp", b"unresolved publication")
    elif damage == "corrupt-orphan":
        (tmp_path / orphan.path).write_bytes(b"bad digest")
    elif damage == "future-artifact":
        store.write_artifact("s", serialize_document({"kind": "assistant_request_archive", "version": 2}))
    else:
        _write(tmp_path / "assistant" / "unknown" / "record.json", b"{}")
    with pytest.raises((BackupVerificationError, SchemaValidationError)):
        AssistantAttachmentCleanup(tmp_path).collect(dry_run=False, quiescent=True)
    assert (tmp_path / orphan.path).exists()


def test_scope_is_checked_even_when_foreign_reference_has_already_been_reached(tmp_path):
    store = AssistantTranscriptStore(str(tmp_path))
    first = store.write_artifact("a", b"first session")
    _save(tmp_path, "a", TranscriptManifest(artifacts=(first,)))
    second = store.write_artifact("b", serialize_document({"foreign": first.to_dict()}))
    _save(tmp_path, "b", TranscriptManifest(artifacts=(second,)))
    with pytest.raises(PathBoundaryError):
        AssistantAttachmentCleanup(tmp_path).collect(dry_run=False, quiescent=True)
    assert (tmp_path / first.path).exists()


def test_live_session_writer_lease_refuses_cleanup_before_unlink(tmp_path):
    store = AssistantTranscriptStore(str(tmp_path))
    orphan = store.write_artifact("s", b"unused")
    ready, release = Event(), Event()

    def hold():
        with session_write_lease(str(tmp_path), "s", OsPersistenceFilesystem()):
            ready.set()
            release.wait(5)

    worker = Thread(target=hold)
    worker.start()
    try:
        assert ready.wait(3)
        with pytest.raises(ReadOnlyWriteRefused):
            AssistantAttachmentCleanup(tmp_path).collect(dry_run=False, quiescent=True)
        assert (tmp_path / orphan.path).exists()
    finally:
        release.set()
        worker.join(5)


def test_retained_manifest_validates_segment_counts_and_digests(tmp_path):
    store = AssistantTranscriptStore(str(tmp_path))
    manifest = store.append("s", TranscriptManifest(), (TranscriptMessage("m", 1, "user", "retained"),))
    cleanup = AssistantAttachmentCleanup(tmp_path)
    assert not cleanup.collect(retained_manifests=(("s", manifest),)).candidates
    damaged = replace(manifest, segments=(replace(manifest.segments[0], size_bytes=1),))
    with pytest.raises(BackupVerificationError):
        cleanup.collect(dry_run=False, quiescent=True, retained_manifests=(("s", damaged),))
    assert (tmp_path / manifest.segments[0].path).exists()


def test_backup_archive_remains_hydratable_after_current_archive_replacement_and_cleanup(tmp_path):
    from transbridge.application.assistant_requests.archival import compact_request_state, hydrate_request_state
    from transbridge.application.assistant_requests.models import ItemStatus, RequestItem, RequestStatus, UserRequest

    store = AssistantTranscriptStore(str(tmp_path))
    request = UserRequest(
        "r",
        "s",
        "full goal",
        (RequestItem("i", "full item", status=ItemStatus.SATISFIED),),
        status=RequestStatus.COMPLETED,
    )
    state, manifest = compact_request_state(
        {"requests": [request.to_dict()]}, TranscriptManifest(), "s", store, keep_recent_terminal=0
    )
    backup = serialize_document(_document("s", manifest, assistant_state=state))
    _write(
        tmp_path / "backups" / "sessions" / SessionId("s").encoded / f"{hashlib.sha256(backup).hexdigest()}.v4.json",
        backup,
    )
    hydrated = hydrate_request_state(state, "s", store)
    hydrated["requests"][0]["stop_reason"] = "late audit detail"
    newer, newer_manifest = compact_request_state(hydrated, manifest, "s", store, keep_recent_terminal=0)
    _save(tmp_path, "s", newer_manifest, assistant_state=newer)
    orphan = store.write_artifact("s", b"abandoned write")
    report = AssistantAttachmentCleanup(tmp_path).collect(dry_run=False, quiescent=True)
    assert report.removed == (orphan.path,)
    assert hydrate_request_state(state, "s", store)["requests"] == [request.to_dict()]
    assert hydrate_request_state(newer, "s", store)["requests"] == hydrated["requests"]


def test_cleanup_unlink_holds_root_shared_repository_mutation_lock(tmp_path, monkeypatch):
    store = AssistantTranscriptStore(str(tmp_path))
    orphan = store.write_artifact("s", b"unused")
    cleanup = AssistantAttachmentCleanup(tmp_path)
    repository = SessionRepository(str(tmp_path), OsPersistenceFilesystem())
    observations = []
    original = cleanup._filesystem.remove

    def probe():
        acquired = repository.mutation_lock.acquire(blocking=False)
        observations.append(acquired)
        if acquired:
            repository.mutation_lock.release()

    def remove(path, *, missing_ok=False):
        thread = Thread(target=probe)
        thread.start()
        thread.join(3)
        original(path, missing_ok=missing_ok)

    monkeypatch.setattr(cleanup._filesystem, "remove", remove)
    assert cleanup.collect(dry_run=False, quiescent=True).removed == (orphan.path,)
    assert observations == [False]


def test_filesystem_cannot_supply_attachment_outside_authorized_root(tmp_path, monkeypatch):
    root = tmp_path / "storage"
    store = AssistantTranscriptStore(str(root))
    orphan = store.write_artifact("s", b"unused")
    outside = tmp_path / "unowned.json"
    outside.write_bytes(b"do not delete")
    cleanup = AssistantAttachmentCleanup(root)
    original = cleanup._filesystem.list_tree_files

    def files(directory):
        return (str(outside),) if directory.endswith("assistant") else original(directory)

    monkeypatch.setattr(cleanup._filesystem, "list_tree_files", files)
    with pytest.raises(PathBoundaryError):
        cleanup.collect(dry_run=False, quiescent=True)
    assert outside.read_bytes() == b"do not delete"
    assert (root / orphan.path).exists()
