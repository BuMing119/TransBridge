"""Content-addressed parsed sources with atomic, dependency-first publication."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import hashlib
import json
import logging
import os
from typing import Any
from uuid import uuid4

from transbridge.application.projects.provisioning_models import PreparedProjectSource

from .source_artifact_codec import (
    SOURCE_ARTIFACT_ABI,
    blob_references,
    decode_prepared_artifact,
    encode_prepared_artifact,
)
from .v2.filesystem import OsPersistenceFilesystem, PersistenceFilesystemPort, RepositoryPaths

_LOGGER = logging.getLogger(__name__)
_FORMAT = "transbridge.prepared-source"


class SourceArtifactMissingError(FileNotFoundError):
    """The referenced artifact manifest is absent."""


class SourceArtifactCorruptError(ValueError):
    """A manifest or its dependency closure is invalid or incomplete."""


class SourceArtifactVersionError(SourceArtifactCorruptError):
    """The artifact was created with an incompatible source contract ABI."""


class ProjectSourceArtifactStore:
    """Persist baseline plus hydration, independently of later Variant edits.

    Each dependency is read once per recovery. Decoded metadata and mutable
    companion TranslationEntry objects are fresh for every recovery.
    """

    def __init__(self, root: str, filesystem: PersistenceFilesystemPort | None = None) -> None:
        self._filesystem = filesystem if filesystem is not None else OsPersistenceFilesystem()
        self._paths = RepositoryPaths(str(root), self._filesystem)

    def put(self, prepared: PreparedProjectSource) -> str:
        collector = _BlobCollector()
        payload = encode_prepared_artifact(prepared, collector.capture)
        blobs = collector.finish()
        manifest = {
            "format": _FORMAT,
            "abi": SOURCE_ARTIFACT_ABI,
            "location": prepared.to_dict()["location"],
            "blobs": sorted(blobs),
            "prepared": payload,
        }
        raw = _json_bytes(manifest)
        artifact_id = _digest(raw)
        for digest, data in blobs.items():
            self._publish(f"blobs/{digest}.bin", data)
        self._publish(f"manifests/{artifact_id}.json", raw)
        return artifact_id

    def get(self, artifact_id: str, *, location: str | None = None) -> PreparedProjectSource:
        return self.read_records(artifact_id, self._read_relative, location=location)

    def iter_records(self, artifact_id: str) -> Iterator[tuple[str, bytes]]:
        """Export exactly this manifest and its verified recursive blob closure."""
        raw, manifest = _read_manifest(artifact_id, self._read_relative)
        yield f"manifests/{artifact_id}.json", raw
        for digest in manifest["blobs"]:
            yield f"blobs/{digest}.bin", _read_blob(digest, self._read_relative)

    @staticmethod
    def read_records(
        artifact_id: str,
        read_bytes_callback: Callable[[str], bytes],
        *,
        location: str | None = None,
    ) -> PreparedProjectSource:
        """Validate an archive in place without writing files or reading live sources."""
        _raw, manifest = _read_manifest(artifact_id, read_bytes_callback, validate_closure=False)
        declared_blobs = set(manifest["blobs"])
        blobs: dict[str, bytes] = {}

        def read_blob(digest: str) -> bytes:
            # Membership also guarantees the digest was validated in the manifest,
            # before constructing a path. Shared packed slices need no rescan.
            if not isinstance(digest, str) or digest not in declared_blobs:
                raise SourceArtifactCorruptError("source artifact blob closure differs from its manifest")
            if digest not in blobs:
                blobs[digest] = _read_blob(digest, read_bytes_callback)
            return blobs[digest]

        try:
            prepared = decode_prepared_artifact(
                manifest["prepared"], read_blob, original_location=manifest["location"], location=location
            )
            if blobs.keys() != declared_blobs:
                raise SourceArtifactCorruptError("source artifact blob closure differs from its manifest")
            return prepared
        except SourceArtifactCorruptError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise SourceArtifactCorruptError(f"source artifact {artifact_id} has invalid prepared contracts") from exc

    def import_records(self, artifact_id: str, read_bytes_callback: Callable[[str], bytes]) -> None:
        """Validate a complete external closure before publishing its manifest last."""
        records: dict[str, bytes] = {}

        def capture(path: str) -> bytes:
            if path not in records:
                records[path] = read_bytes_callback(path)
            return records[path]

        self.read_records(artifact_id, capture)
        manifest_path = f"manifests/{artifact_id}.json"
        for path, data in records.items():
            if path != manifest_path:
                self._publish(path, data)
        self._publish(manifest_path, records[manifest_path])

    def _path(self, relative: str) -> str:
        return self._paths.guard(os.path.join(self._paths.root, "source_artifacts", *relative.split("/")))

    def _read_relative(self, relative: str) -> bytes:
        path = self._path(relative)
        if not self._filesystem.exists(path):
            raise FileNotFoundError(path)
        return self._filesystem.read_bytes(path)

    def _publish(self, relative: str, data: bytes) -> None:
        destination = self._path(relative)
        if self._filesystem.exists(destination):
            if self._filesystem.read_bytes(destination) != data:
                raise SourceArtifactCorruptError(f"immutable source artifact record differs: {relative}")
            return
        stage = self._path(f".staging/{uuid4().hex}.tmp")
        self._filesystem.make_dirs(os.path.dirname(destination))
        self._filesystem.make_dirs(os.path.dirname(stage))
        try:
            self._filesystem.write_bytes(stage, data)
            if self._filesystem.read_bytes(stage) != data:
                raise OSError(f"source artifact staging verification failed: {relative}")
            self._filesystem.replace_durable(stage, destination)
        except Exception:
            try:
                self._filesystem.remove(stage, missing_ok=True)
            except OSError:
                _LOGGER.exception("Unable to remove failed source artifact staging file: %s", stage)
            raise


def _read_manifest(
    artifact_id: str, read: Callable[[str], bytes], *, validate_closure: bool = True
) -> tuple[bytes, dict[str, Any]]:
    _validate_digest(artifact_id)
    try:
        raw = read(f"manifests/{artifact_id}.json")
    except (FileNotFoundError, KeyError) as exc:
        raise SourceArtifactMissingError(f"source artifact manifest is missing: {artifact_id}") from exc
    if _digest(raw) != artifact_id:
        raise SourceArtifactCorruptError(f"source artifact manifest digest mismatch: {artifact_id}")
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(manifest, dict) or manifest.get("format") != _FORMAT:
            raise ValueError("invalid source artifact format")
        if type(manifest.get("abi")) is not int or manifest["abi"] != SOURCE_ARTIFACT_ABI:
            raise SourceArtifactVersionError(f"source artifact ABI is unsupported: {manifest.get('abi')}")
        if set(manifest) != {"format", "abi", "location", "blobs", "prepared"}:
            raise ValueError("invalid source artifact manifest fields")
        if not isinstance(manifest["location"], str) or not manifest["location"].strip():
            raise ValueError("invalid source artifact location")
        blobs = manifest["blobs"]
        if not isinstance(blobs, list):
            raise ValueError("invalid source artifact blob list")
        for digest in blobs:
            _validate_digest(digest)
        if blobs != sorted(set(blobs)):
            raise ValueError("source artifact blob list is not unique and sorted")
        if validate_closure and set(blobs) != blob_references(manifest["prepared"]):
            raise ValueError("source artifact blob closure differs from its manifest")
    except SourceArtifactVersionError:
        raise
    except (UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise SourceArtifactCorruptError(f"source artifact manifest is invalid: {artifact_id}") from exc
    return raw, manifest


def _read_blob(digest: str, read: Callable[[str], bytes]) -> bytes:
    # Both callers obtain this digest from the validated manifest blob list.
    try:
        content = read(f"blobs/{digest}.bin")
    except (FileNotFoundError, KeyError) as exc:
        raise SourceArtifactCorruptError(f"source artifact dependency is missing: {digest}") from exc
    if _digest(content) != digest:
        raise SourceArtifactCorruptError(f"source artifact blob digest mismatch: {digest}")
    return content


def _validate_digest(value: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("source artifact identity must be a lowercase SHA-256 digest")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate source artifact JSON key: {key}")
        result[key] = value
    return result


class _BlobCollector:
    """Keep large content separate and pack tiny localized-record byte slices.

    A localized table may contain hundreds of thousands of raw payload/chunk
    values. They must not turn into hundreds of thousands of small disk files.
    Pack references are finalized before the manifest is serialized.
    """

    _SMALL_LIMIT = 64 * 1024
    _PACK_LIMIT = 8 * 1024 * 1024

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._large: dict[int, tuple[bytes, dict[str, Any]]] = {}
        self._small: dict[bytes, dict[str, Any]] = {}
        self._pending = bytearray()
        self._references: list[dict[str, Any]] = []

    def capture(self, content: bytes) -> dict[str, Any]:
        if len(content) > self._SMALL_LIMIT:
            previous = self._large.get(id(content))
            if previous is not None:
                return previous[1]
            digest = _digest(content)
            self._blobs.setdefault(digest, content)
            reference = {"$blob": digest}
            self._large[id(content)] = content, reference
            return reference
        reference = self._small.get(content)
        if reference is not None:
            return reference
        if len(self._pending) + len(content) > self._PACK_LIMIT:
            self._flush()
        reference = {"$blob": None, "offset": len(self._pending), "size": len(content)}
        self._pending.extend(content)
        self._references.append(reference)
        self._small[content] = reference
        return reference

    def finish(self) -> dict[str, bytes]:
        self._flush()
        return self._blobs

    def _flush(self) -> None:
        if not self._references:
            return
        content = bytes(self._pending)
        digest = _digest(content)
        self._blobs.setdefault(digest, content)
        for reference in self._references:
            reference["$blob"] = digest
        self._references.clear()
        self._pending.clear()
