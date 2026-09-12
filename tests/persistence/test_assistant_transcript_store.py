from __future__ import annotations

from dataclasses import replace

import pytest

from transbridge.application.assistant_requests.transcript import TranscriptManifest, TranscriptMessage
from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.models import AtomicWriteError, BackupVerificationError, PathBoundaryError


def test_full_history_survives_more_than_twenty_turns_and_orphan_segment(tmp_path) -> None:
    store = AssistantTranscriptStore(str(tmp_path))
    manifest = TranscriptManifest()
    for index in range(1, 26):
        manifest = store.append(
            "session-a", manifest, (TranscriptMessage(f"input-{index}", index, "user", str(index)),)
        )
    assert len(store.read("session-a", manifest)) == 25
    assert store.read("session-a", manifest)[0].content == "1"
    previous = manifest
    unpublished = store.append("session-a", manifest, (TranscriptMessage("input-26", 26, "user", "not committed"),))
    # A crash before Session CAS leaves a segment, but old manifest remains authoritative.
    reopened = AssistantTranscriptStore(str(tmp_path))
    assert len(reopened.read("session-a", previous)) == 25
    assert len(reopened.read("session-a", unpublished)) == 26
    assert manifest.input_watermark == 0


def test_artifact_reads_require_matching_scope_and_digest(tmp_path) -> None:
    store = AssistantTranscriptStore(str(tmp_path))
    reference = store.write_artifact("session-a", b"full tool output")
    assert store.read_artifact("session-a", reference) == b"full tool output"
    assert store.write_artifact("session-a", b"full tool output") == reference
    with pytest.raises(PathBoundaryError, match="Session"):
        store.read_artifact("session-b", reference)
    (tmp_path / reference.path).write_bytes(b"corruption")
    with pytest.raises(BackupVerificationError, match="digest"):
        store.read_artifact("session-a", reference)
    with pytest.raises(BackupVerificationError):
        store.write_artifact("session-a", b"full tool output")


def test_failed_attachment_publication_never_changes_prior_manifest(tmp_path) -> None:
    class FailingFilesystem(OsPersistenceFilesystem):
        def replace_durable(self, source, destination):
            raise OSError("injected publication failure")

    manifest = TranscriptManifest()
    store = AssistantTranscriptStore(str(tmp_path), FailingFilesystem())
    with pytest.raises(AtomicWriteError, match="publication"):
        store.append("session-a", manifest, (TranscriptMessage("first", 1, "user", "one"),))
    assert store.read("session-a", manifest) == ()
    assert list((tmp_path / ".staging").iterdir()) == []


def test_corrupt_or_missing_committed_segment_is_not_empty_history(tmp_path) -> None:
    store = AssistantTranscriptStore(str(tmp_path))
    manifest = store.append("session-a", TranscriptManifest(), (TranscriptMessage("first", 1, "user", "one"),))
    with pytest.raises(BackupVerificationError):
        store.read("session-a", replace(manifest, segments=(replace(manifest.segments[0], size_bytes=1),)))
    (tmp_path / manifest.segments[0].path).unlink()
    with pytest.raises(BackupVerificationError, match="missing"):
        store.read("session-a", manifest)
