"""Content-addressed, root-confined transcript storage with explicit commit refs."""

from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from uuid import uuid4

from transbridge.application.assistant_requests.transcript import (
    AttachmentRef,
    TranscriptManifest,
    TranscriptMessage,
    validate_message_order,
)

from .v2.filesystem import OsPersistenceFilesystem, PersistenceFilesystemPort, RepositoryPaths
from .v2.ids import SessionId
from .v2.models import AtomicWriteError, BackupVerificationError, PathBoundaryError
from .v2.repository import _mutation_lock_for
from .v2.schema import parse_json_bytes, serialize_document
from .v2.session_write_lease import session_write_lease


@lru_cache(maxsize=16)
def _decode_segment(raw: bytes) -> tuple[TranscriptMessage, ...]:
    """Cache pure decoding only; callers still read and verify every disk access."""
    document = parse_json_bytes(raw)
    items = document.get("messages")
    if not isinstance(items, list):
        raise BackupVerificationError("transcript segment messages must be a list")
    try:
        return tuple(TranscriptMessage.from_dict(item) for item in items)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupVerificationError("transcript segment contains an invalid message") from exc


class AssistantTranscriptStore:
    """Attachments become committed only when their manifest is saved by Session CAS.

    Orphans are deliberately retained: current and backup manifests may refer to
    immutable files, so neither a crash nor a new writer may delete by age.
    """

    def __init__(self, root: str, filesystem: PersistenceFilesystemPort | None = None) -> None:
        self._filesystem = filesystem or OsPersistenceFilesystem()
        self._paths = RepositoryPaths(root, self._filesystem)
        self._lock = _mutation_lock_for(root, self._filesystem)

    def append(
        self,
        session_id: str,
        manifest: TranscriptManifest,
        messages: tuple[TranscriptMessage, ...],
    ) -> TranscriptManifest:
        if not messages:
            return manifest
        with self._lock, session_write_lease(self._paths.root, session_id, self._filesystem):
            existing = self.read(session_id, manifest)
            combined = existing + tuple(messages)
            validate_message_order(combined)
            raw = serialize_document({"messages": [message.to_dict() for message in messages]})
            reference = self._write(session_id, "segments", raw, count=len(messages))
            return TranscriptManifest(
                manifest.segments + (reference,), len(combined), manifest.input_watermark, manifest.artifacts
            )

    def read(self, session_id: str, manifest: TranscriptManifest) -> tuple[TranscriptMessage, ...]:
        messages: list[TranscriptMessage] = []
        for reference in manifest.segments:
            raw = self._read(session_id, "segments", reference)
            # Limit retained bytes as well as entry count. Even a cache hit has
            # already passed _read's scope, length and SHA256 verification.
            # Tiny incremental segments must not evict the large historical
            # segment on every new input in a long-running conversation.
            items = _decode_segment(raw) if 4096 <= len(raw) <= 4 * 1024 * 1024 else _decode_segment.__wrapped__(raw)
            if len(items) != reference.count:
                raise BackupVerificationError("transcript segment count does not match its manifest")
            messages.extend(items)
        result = tuple(messages)
        try:
            validate_message_order(result)
        except ValueError as exc:
            raise BackupVerificationError("transcript segment ordering is invalid") from exc
        if len(result) != manifest.last_sequence:
            raise BackupVerificationError("transcript manifest sequence does not match its records")
        return result

    def validate(self, session_id: str, manifest: TranscriptManifest) -> None:
        self.read(session_id, manifest)
        for reference in manifest.artifacts:
            self.read_artifact(session_id, reference)

    def write_artifact(self, session_id: str, data: bytes) -> AttachmentRef:
        with self._lock, session_write_lease(self._paths.root, session_id, self._filesystem):
            return self._write(session_id, "artifacts", data)

    def read_artifact(self, session_id: str, reference: AttachmentRef) -> bytes:
        return self._read(session_id, "artifacts", reference)

    def read_attachment(self, session_id: str, reference: AttachmentRef) -> bytes:
        """Verify a manifest/retention edge without accepting a caller-built path."""
        parts = reference.path.split("/")
        if len(parts) != 4 or parts[2] not in {"segments", "artifacts"}:
            raise PathBoundaryError("unknown assistant attachment content type")
        raw = self._read(session_id, parts[2], reference)
        if parts[2] == "segments" and len(_decode_segment(raw)) != reference.count:
            raise BackupVerificationError("retained segment count does not match its reference")
        return raw

    def _reference(self, session_id: str, kind: str, digest: str, size: int, count: int) -> AttachmentRef:
        session = SessionId(session_id)
        path = f"assistant/{session.encoded}/{kind}/{digest}.json"
        return AttachmentRef(path, digest, size, count)

    def _path(self, session_id: str, kind: str, reference: AttachmentRef) -> str:
        expected = self._reference(session_id, kind, reference.digest, reference.size_bytes, reference.count)
        if reference.path != expected.path:
            raise PathBoundaryError("attachment does not belong to the requested Session and content type")
        return self._paths.guard(os.path.join(self._paths.root, *reference.path.split("/")))

    def _read(self, session_id: str, kind: str, reference: AttachmentRef) -> bytes:
        path = self._path(session_id, kind, reference)
        try:
            raw = self._filesystem.read_bytes(path)
        except FileNotFoundError as exc:
            raise BackupVerificationError("committed assistant attachment is missing") from exc
        if len(raw) != reference.size_bytes or hashlib.sha256(raw).hexdigest() != reference.digest:
            raise BackupVerificationError("assistant attachment digest does not match the committed manifest")
        return raw

    def _write(self, session_id: str, kind: str, data: bytes, *, count: int = 0) -> AttachmentRef:
        digest = hashlib.sha256(data).hexdigest()
        reference = self._reference(session_id, kind, digest, len(data), count)
        path = self._path(session_id, kind, reference)
        if self._filesystem.exists(path):
            self._read(session_id, kind, reference)
            return reference
        # Unique staging names avoid one process deleting another writer's staging
        # file. Publishing identical content is safe across processes.
        stage = self._paths.guard(os.path.join(self._paths.root, ".staging", f"assistant-{uuid4().hex}.tmp"))
        self._filesystem.make_dirs(os.path.dirname(stage))
        self._filesystem.make_dirs(os.path.dirname(path))
        try:
            self._filesystem.write_bytes(stage, data)
            if self._filesystem.read_bytes(stage) != data:
                raise AtomicWriteError("assistant attachment staging verification failed")
            self._filesystem.replace_durable(stage, path)
        except Exception as exc:
            self._filesystem.remove(stage, missing_ok=True)
            raise AtomicWriteError("assistant attachment publication failed") from exc
        self._read(session_id, kind, reference)
        return reference
