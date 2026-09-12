"""Explicit offline reclamation of verified, unreferenced assistant attachments."""

from __future__ import annotations

import base64
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import os
import re

from transbridge.application.assistant_requests.archival import hydrate_request_state
from transbridge.application.assistant_requests.transcript import AttachmentRef, TranscriptManifest

from .assistant_transcript_store import AssistantTranscriptStore
from .v2.filesystem import OsPersistenceFilesystem, RepositoryPaths
from .v2.ids import SessionId, SessionRef
from .v2.migration import migrate_to_current
from .v2.models import SCHEMA_VERSION, BackupVerificationError, PathBoundaryError, SchemaValidationError
from .v2.repository import _mutation_lock_for
from .v2.schema import parse_json_bytes, validate_v2, version_of
from .v2.session_write_lease import session_write_lease


@dataclass(frozen=True)
class AttachmentCleanupReport:
    dry_run: bool
    candidates: tuple[str, ...]
    removed: tuple[str, ...]
    referenced: tuple[str, ...]
    ignored: tuple[str, ...]
    reclaimable_bytes: int
    source_count: int


def _session_identity(encoded):
    try:
        if not encoded.startswith("id-"):
            raise ValueError("missing opaque identity prefix")
        payload = encoded[3:]
        identity = SessionId(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("ascii"))
        if identity.encoded != encoded:
            raise ValueError("noncanonical encoded identity")
        return identity.value
    except (ValueError, UnicodeError) as exc:
        raise BackupVerificationError("assistant attachment ownership cannot be verified") from exc


class AssistantAttachmentCleanup:
    """No automatic cleanup: callers must stop writers and supply retained refs.

    ``quiescent=True`` is the caller's explicit assertion of offline maintenance,
    not proof that other processes stopped. Root and per-Session locks additionally
    exclude cooperating writers. Unpublished references held outside a transaction
    must be passed as retained references; they cannot be inferred from disk.
    """

    def __init__(self, root, filesystem=None):
        self._filesystem = filesystem or OsPersistenceFilesystem()
        self._paths = RepositoryPaths(str(root), self._filesystem)
        self._lock = _mutation_lock_for(str(root), self._filesystem)
        self._store = AssistantTranscriptStore(str(root), self._filesystem)

    def collect(self, *, dry_run=True, quiescent=False, retained_manifests=(), retained_artifacts=()):
        """Preview by default; never reuse a stale preview as deletion authority.

        Retained inputs are ``(session_id, TranscriptManifest/AttachmentRef)``
        pairs. Each invocation rescans all Session/backup/retained documents.
        Validation failures occur before the first unlink and abort reclamation.
        """
        if type(dry_run) is not bool or type(quiescent) is not bool:
            raise ValueError("cleanup flags must be explicit booleans")
        if not dry_run and not quiescent:
            raise ValueError("applying cleanup requires offline maintenance (quiescent=True)")
        retained_manifests, retained_artifacts = tuple(retained_manifests), tuple(retained_artifacts)
        with self._lock:
            sources = self._sources()
            owned, ignored = self._owned_files()
            sessions = {record[0] for record in owned.values()}
            sessions.update(self._document(path)[0] for path in sources)
            sessions.update(sid for sid, _ in (*retained_manifests, *retained_artifacts))
            with ExitStack() as stack:
                for sid in sorted(sessions):
                    stack.enter_context(session_write_lease(self._paths.root, sid, self._filesystem))
                if sources != self._sources():
                    raise BackupVerificationError("Session retention sources changed while acquiring cleanup leases")
                return self._collect(sources, owned, ignored, retained_manifests, retained_artifacts, dry_run)

    def _collect(self, sources, owned, ignored, retained_manifests, retained_artifacts, dry_run):
        reached, fingerprints = {}, {}
        for path in sources:
            sid, document, raw = self._document(path)
            fingerprints[path] = hashlib.sha256(raw).hexdigest()
            data = document["data"]
            manifest = self._manifest(data.get("transcript_manifest", {}))
            self._retain_manifest(sid, manifest, reached)
            state = data.get("assistant_state", {})
            if state.get("request_archives"):
                hydrated = hydrate_request_state(state, sid, self._store)
                committed = {ref.path for ref in manifest.artifacts}
                if any(entry["artifact"]["path"] not in committed for entry in state["request_archives"]):
                    raise BackupVerificationError("request archive lacks its committed manifest reference")
                self._retain_values(sid, hydrated, reached)
            self._retain_values(sid, data, reached)
            original = parse_json_bytes(raw)
            original_data = original.get("data", original)
            if isinstance(original_data, dict) and "transcript_manifest" in original_data:
                self._retain_manifest(sid, self._manifest(original_data["transcript_manifest"]), reached)
            self._retain_values(sid, original, reached)
        for sid, manifest in retained_manifests:
            if not isinstance(manifest, TranscriptManifest):
                raise TypeError("retained manifests must be typed TranscriptManifest values")
            self._retain_manifest(sid, manifest, reached)
        for sid, reference in retained_artifacts:
            if not isinstance(reference, AttachmentRef):
                raise TypeError("retained artifacts must be typed AttachmentRef values")
            self._retain(sid, reference, reached)
        candidates = tuple(sorted(set(owned) - set(reached)))
        # Verify every candidate before deleting any; malformed/foreign files
        # cannot establish ownership merely by residing below assistant/.
        sizes = {}
        for path in candidates:
            sid, reference = owned[path]
            sizes[path] = len(self._store.read_attachment(sid, reference))
        for path, fingerprint in fingerprints.items():
            if hashlib.sha256(self._filesystem.read_bytes(path)).hexdigest() != fingerprint:
                raise BackupVerificationError("Session retention source changed during cleanup")
        if sources != self._sources():
            raise BackupVerificationError("Session retention sources changed during cleanup")
        removed = []
        if not dry_run:
            for path in candidates:
                absolute = self._paths.guard(os.path.join(self._paths.root, *path.split("/")))
                self._filesystem.remove(absolute, missing_ok=False)
                removed.append(path)
        return AttachmentCleanupReport(
            dry_run,
            candidates,
            tuple(removed),
            tuple(sorted(reached)),
            tuple(ignored),
            sum(sizes.values()),
            len(sources),
        )

    def _sources(self):
        sources = []
        for directory in ("sessions", "backups/sessions", "retained/sessions", "quarantine/sessions"):
            root = self._paths.guard(os.path.join(self._paths.root, *directory.split("/")))
            for path in self._filesystem.list_tree_files(root):
                path = self._paths.guard(path)
                if path.endswith(".report.json") and directory == "quarantine/sessions":
                    # A quarantine report means its paired payload must remain;
                    # validate both, rather than treating the report as a Session.
                    report = parse_json_bytes(self._filesystem.read_bytes(path))
                    payload = path.removesuffix(".report.json") + ".json"
                    if (
                        report.get("entity_type") != "quarantine-report"
                        or version_of(report) > SCHEMA_VERSION
                        or not self._filesystem.exists(payload)
                        or hashlib.sha256(self._filesystem.read_bytes(payload)).hexdigest()
                        != report.get("data", {}).get("original_hash")
                    ):
                        raise BackupVerificationError("quarantine retention report cannot be verified")
                    continue
                if not path.endswith(".json"):
                    raise BackupVerificationError("unknown file format in Session retention sources")
                sources.append(path)
        staging = self._paths.guard(os.path.join(self._paths.root, ".staging"))
        if self._filesystem.list_tree_files(staging):
            raise BackupVerificationError("unresolved staging files prevent proving attachment reachability")
        return tuple(sorted(sources))

    def _document(self, path):
        raw = self._filesystem.read_bytes(path)
        document = parse_json_bytes(raw)
        relative = os.path.relpath(path, self._paths.root).replace("\\", "/").split("/")
        expected_digest = None
        if relative[0] == "sessions" and len(relative) == 2:
            sid = _session_identity(relative[1].removesuffix(".json"))
        elif relative[:2] == ["backups", "sessions"] and len(relative) == 4:
            sid = _session_identity(relative[2])
            match = re.fullmatch(r"([0-9a-f]{64})\.v([1-9][0-9]*)\.json", relative[3])
            if not match or int(match[2]) != version_of(document):
                raise BackupVerificationError("unknown Session backup identity or version")
            expected_digest = match[1]
        elif relative[:2] == ["quarantine", "sessions"] and len(relative) == 3:
            encoded, _, expected_digest = relative[2].removesuffix(".json").rpartition("-")
            sid = _session_identity(encoded)
        elif relative[:2] == ["retained", "sessions"]:
            sid = SessionId(document.get("id", "")).value
        else:
            raise BackupVerificationError("unknown Session retention layout")
        if expected_digest and hashlib.sha256(raw).hexdigest() != expected_digest:
            raise BackupVerificationError("Session backup/retained payload checksum does not match its identity")
        if version_of(document) > SCHEMA_VERSION:
            raise BackupVerificationError("future Session formats prevent proving attachment reachability")
        reference = SessionRef(SessionId(sid))
        migrated = (
            migrate_to_current(document, reference).document if version_of(document) < SCHEMA_VERSION else document
        )
        validate_v2(migrated, reference)
        return sid, migrated, raw

    @staticmethod
    def _manifest(data):
        if not isinstance(data, dict) or set(data) - {"segments", "artifacts", "last_sequence", "input_watermark"}:
            raise BackupVerificationError("unknown transcript manifest format")
        return TranscriptManifest.from_dict(data)

    def _retain_manifest(self, sid, manifest, reached):
        self._store.validate(sid, manifest)
        for reference in (*manifest.segments, *manifest.artifacts):
            self._retain(sid, reference, reached)

    def _retain(self, sid, reference, reached):
        if not reference.path.startswith(f"assistant/{SessionId(sid).encoded}/"):
            raise PathBoundaryError("retained attachment belongs to another Session")
        if reference.path in reached:
            if reached[reference.path] != reference:
                raise BackupVerificationError("retained attachment has inconsistent references")
            return
        raw = self._store.read_attachment(sid, reference)
        reached[reference.path] = reference
        # Artifacts may be opaque tool bytes. JSON artifacts and segments can
        # additionally retain typed attachment references in their structured data.
        document = self._artifact_document(raw)
        if document is None:
            return
        self._retain_values(sid, document, reached)

    @staticmethod
    def _artifact_document(raw):
        try:
            document = parse_json_bytes(raw)
        except SchemaValidationError:
            return None  # Opaque, verified tool bytes have no structured retention edges.
        if str(document.get("kind", "")).startswith("assistant_"):
            archive = document.get("kind") == "assistant_request_archive" and document.get("version") == 1
            context = (
                document.get("kind") == "assistant_context"
                and type(document.get("schema_version")) is int
                and document["schema_version"] == 1
                and document.get("mode") in {"base", "append"}
            )
            if not (archive or context):
                raise BackupVerificationError("unknown internal assistant artifact format")
        return document

    def _retain_values(self, sid, value, reached):
        if isinstance(value, dict):
            if isinstance(value.get("path"), str) and value["path"].startswith("assistant/"):
                self._retain(sid, AttachmentRef.from_dict(value), reached)
            else:
                for item in value.values():
                    self._retain_values(sid, item, reached)
        elif isinstance(value, (tuple, list)):
            for item in value:
                self._retain_values(sid, item, reached)

    def _owned_files(self):
        directory = self._paths.guard(os.path.join(self._paths.root, "assistant"))
        owned, ignored = {}, []
        for path in self._filesystem.list_tree_files(directory):
            path = self._paths.guard(path)
            relative = os.path.relpath(path, self._paths.root).replace("\\", "/")
            parts = relative.split("/")
            if (
                len(parts) != 4
                or parts[2] not in {"segments", "artifacts"}
                or not re.fullmatch(r"[a-f0-9]{64}\.json", parts[3])
            ):
                if relative.endswith(".json"):
                    raise BackupVerificationError("unknown assistant attachment layout prevents cleanup")
                ignored.append(relative)
                continue
            sid = _session_identity(parts[1])
            raw = self._filesystem.read_bytes(path)
            count = 0
            if parts[2] == "segments":
                document = parse_json_bytes(raw)
                if not isinstance(document.get("messages"), list):
                    raise BackupVerificationError("unknown transcript segment format")
                count = len(document["messages"])
            else:
                self._artifact_document(raw)
            reference = AttachmentRef(relative, parts[3][:-5], len(raw), count)
            self._store.read_attachment(sid, reference)
            owned[relative] = (sid, reference)
        return owned, ignored
