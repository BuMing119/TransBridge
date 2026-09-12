from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import multiprocessing
from threading import Barrier

import pytest

from transbridge.application.assistant_requests.transcript import TranscriptManifest, TranscriptMessage
from transbridge.application.contracts import DomainError, RequestContext
from transbridge.application.sessions import ControllerSnapshot, SessionSnapshot
from transbridge.application.tasks.models import OwnerRef
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.persistence.session_lifecycle import V2SessionSnapshotRepository
from transbridge.persistence.v2 import QuarantineResult, SessionId, SessionRef, SessionRepository
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.models import AtomicWriteError, ReadOnlyWriteRefused, SchemaValidationError


def _snapshot() -> SessionSnapshot:
    return SessionSnapshot(
        ref=SessionRef(SessionId("session-a")),
        name="Session A",
        owner=OwnerRef("owner", "gui", session_id="session-a"),
        messages=(),
        backend_history=(),
        backend_summary=None,
        controller=ControllerSnapshot(),
        project_id=None,
        variant_id=None,
        approvals=(),
        jobs=(),
        revision=0,
        created_at="2026-09-12T00:00:00Z",
        last_active_at="2026-09-12T00:00:00Z",
    )


def test_snapshot_request_state_and_manifest_round_trip_without_mutable_aliases(tmp_path) -> None:
    repository = SessionRepository(str(tmp_path), OsPersistenceFilesystem())
    transcript = AssistantTranscriptStore(str(tmp_path))
    manifest = transcript.append("session-a", TranscriptManifest(), (TranscriptMessage("input", 1, "user", "hi"),))
    state = {"requests": [{"request_id": "request-a", "items": ["one"]}]}
    snapshot = replace(_snapshot(), assistant_state=state, transcript_manifest=manifest.to_dict())
    state["requests"][0]["items"].append("other")
    repository.save(snapshot.ref, snapshot.to_dto())
    restored = SessionSnapshot.from_dto(repository.load(snapshot.ref).value)
    assert restored == snapshot
    exported = restored.assistant_data()
    exported["requests"][0]["items"].append("third")
    assert restored.assistant_data()["requests"][0]["items"] == ["one"]
    assert restored.transcript_data() == manifest.to_dict()


def test_two_repository_instances_serialize_revision_check_and_save(tmp_path) -> None:
    repositories = [SessionRepository(str(tmp_path), OsPersistenceFilesystem()) for _ in range(2)]
    original = _snapshot()
    repositories[0].save(original.ref, original.to_dto())
    barrier = Barrier(2)

    def save(index: int):
        adapter = V2SessionSnapshotRepository(repositories[index])
        candidate = replace(original, revision=1, assistant_state={"winner": index})
        barrier.wait(timeout=5)
        try:
            adapter.save(candidate, expected_revision=0, context=RequestContext("owner", session_id="session-a"))
            return index
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(save, range(2)))
    assert outcomes.count("SESSION_REVISION_CONFLICT") == 1
    winner = next(value for value in outcomes if isinstance(value, int))
    current = SessionSnapshot.from_dto(repositories[0].load(original.ref).value)
    assert current.revision == 1
    assert current.assistant_data() == {"winner": winner}


def _hold_lease(root, ready, release) -> None:
    repository = SessionRepository(root, OsPersistenceFilesystem())
    with repository.write_transaction(SessionRef(SessionId("session-a"))):
        ready.set()
        release.wait(15)


def test_other_process_writer_is_refused_then_recovers_after_lease_release(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_lease, args=(str(tmp_path), ready, release))
    process.start()
    try:
        assert ready.wait(10), "child did not acquire its Session lease"
        repository = SessionRepository(str(tmp_path), OsPersistenceFilesystem())
        with pytest.raises(ReadOnlyWriteRefused) as caught:
            repository.save(_snapshot().ref, _snapshot().to_dto())
        assert caught.value.code == "SESSION_WRITER_BUSY"
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    repository.save(_snapshot().ref, _snapshot().to_dto())


def test_damaged_committed_segment_quarantines_manifest_and_refuses_save(tmp_path) -> None:
    repository = SessionRepository(str(tmp_path), OsPersistenceFilesystem())
    store = AssistantTranscriptStore(str(tmp_path))
    manifest = store.append("session-a", TranscriptManifest(), (TranscriptMessage("input", 1, "user", "hi"),))
    snapshot = replace(_snapshot(), transcript_manifest=manifest.to_dict())
    repository.save(snapshot.ref, snapshot.to_dto())
    original = (tmp_path / "sessions" / f"{snapshot.ref.identity.encoded}.json").read_bytes()
    (tmp_path / manifest.segments[0].path).write_bytes(b"broken")
    result = repository.load(snapshot.ref)
    assert isinstance(result, QuarantineResult)
    assert result.reason_code == "SESSION_ATTACHMENTS_INVALID"
    with pytest.raises(SchemaValidationError, match="read-only"):
        repository.save(snapshot.ref, snapshot.to_dto())
    assert (tmp_path / "sessions" / f"{snapshot.ref.identity.encoded}.json").read_bytes() == original


def test_missing_manifest_artifact_keeps_session_read_only(tmp_path) -> None:
    repository = SessionRepository(str(tmp_path), OsPersistenceFilesystem())
    store = AssistantTranscriptStore(str(tmp_path))
    reference = store.write_artifact("session-a", b"full tool result")
    manifest = TranscriptManifest(artifacts=(reference,))
    snapshot = replace(_snapshot(), transcript_manifest=manifest.to_dict())
    repository.save(snapshot.ref, snapshot.to_dto())
    (tmp_path / reference.path).unlink()
    result = repository.load(snapshot.ref)
    assert isinstance(result, QuarantineResult)
    assert result.reason_code == "SESSION_ATTACHMENTS_INVALID"


def test_manifest_publish_failure_keeps_previous_references_and_watermark(tmp_path) -> None:
    class FailingFilesystem(OsPersistenceFilesystem):
        fail = False

        def replace_durable(self, source, destination):
            if self.fail:
                raise OSError("injected durable manifest failure")
            super().replace_durable(source, destination)

    filesystem = FailingFilesystem()
    repository = SessionRepository(str(tmp_path), filesystem)
    original = _snapshot()
    repository.save(original.ref, original.to_dto())
    store = AssistantTranscriptStore(str(tmp_path))
    manifest = store.append("session-a", TranscriptManifest(), (TranscriptMessage("input", 1, "user", "hi"),))
    candidate = replace(original, revision=1, transcript_manifest=replace(manifest, input_watermark=1).to_dict())
    filesystem.fail = True
    with pytest.raises(AtomicWriteError):
        repository.save(candidate.ref, candidate.to_dto())
    restored = SessionSnapshot.from_dto(repository.load(original.ref).value)
    assert restored == original
    assert (tmp_path / manifest.segments[0].path).exists()
