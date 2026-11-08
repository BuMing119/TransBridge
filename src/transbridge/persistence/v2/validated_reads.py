"""Operation-local evidence that exact record bytes passed schema validation."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
from threading import RLock
from typing import Any

from .ids import EntityRef
from .models import LoadedRecord, MigrationReport, PersistenceDto, SchemaEnvelope
from .project_record_guard import RecordVersion
from .schema import parse_json_bytes
from .variant_validation import reuse_variant_preimage


@dataclass(frozen=True, slots=True)
class _Evidence:
    dto_type: type[PersistenceDto]
    version: RecordVersion
    migrated: bool
    migration_report: MigrationReport | None


class ValidatedReads:
    """Evidence owned by one operation, optionally carried between its threads.

    Only immutable bytes and metadata are retained. Every public read gets its
    own parsed data, so mutating a returned DTO cannot change trusted evidence.
    Repository identity, reference and exact bytes all participate in the key.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[object, EntityRef, bytes], _Evidence] = {}
        self._preimages: dict[tuple[object, EntityRef], bytes] = {}
        self._lock = RLock()


_READS: ContextVar[ValidatedReads | None] = ContextVar("persistence_validated_reads", default=None)


@contextmanager
def reuse_validated_reads(evidence: ValidatedReads | None = None) -> Iterator[ValidatedReads]:
    """Reuse exact-byte validation within an operation, including nested scopes.

    Filesystem reads, revision checks and publication readback remain mandatory.
    An explicit container can carry evidence from preparation to activation;
    context inheritance across threads is neither needed nor assumed.
    """
    reads = evidence if evidence is not None else _READS.get()
    if reads is None:
        reads = ValidatedReads()
    token = _READS.set(reads)
    try:
        yield reads
    finally:
        _READS.reset(token)


def _validated_evidence(repository: object, ref: EntityRef, raw: bytes) -> _Evidence | None:
    reads = _READS.get()
    if reads is None:
        return None
    with reads._lock:
        return reads._records.get((repository, ref, raw))


def validated_record_version(repository: object, ref: EntityRef, raw: bytes) -> RecordVersion | None:
    """Return the revision and digest of previously validated exact bytes."""
    previous = _validated_evidence(repository, ref, raw)
    return None if previous is None else previous.version


def validated_read(repository: object, ref: EntityRef, raw: bytes) -> LoadedRecord | None:
    previous = _validated_evidence(repository, ref, raw)
    if previous is None:
        return None
    # Parsing the immutable, validated bytes is faster than deep-copying large
    # nested DTOs and does not share mutable data with any earlier caller.
    document = parse_json_bytes(raw)
    envelope = SchemaEnvelope(
        schema_version=document["schema_version"],
        entity_type=ref.kind,
        identity=document["id"],
        revision=document["revision"],
        data=document["data"],
    )
    return LoadedRecord(
        ref,
        previous.dto_type(envelope),
        previous.version.source_hash,
        migrated=previous.migrated,
        migration_report=previous.migration_report,
    )


def remember_validated_read(
    repository: object, raw: bytes, record: LoadedRecord, *, full_schema: bool = False
) -> LoadedRecord:
    """Remember only bytes already validated as their current-schema document."""
    reads = _READS.get()
    if reads is not None:
        with reads._lock:
            reads._records[repository, record.ref, raw] = _Evidence(
                type(record.value), RecordVersion.from_record(record), record.migrated, record.migration_report
            )
            if full_schema:
                reads._preimages[repository, record.ref] = raw
    return record


def decode_validated_read(
    repository: object,
    ref: EntityRef,
    raw: bytes,
    *,
    validate: Callable[[dict[str, Any], EntityRef], PersistenceDto],
    document: dict[str, Any] | None = None,
    remember: bool = True,
    reuse_unchanged: bool = False,
) -> LoadedRecord:
    """Decode exact bytes, sharing validation while preserving local validators.

    Callers supply their validator so existing diagnostics/instrumentation keep
    observing the actual validation boundary. Proposed writes may defer adding
    evidence until publication succeeds by passing ``remember=False``.
    A caller that has just parsed ``raw`` to inspect its schema version may
    supply that same document.
    """
    reads = _READS.get()
    if reads is None:
        with reuse_variant_preimage(ref, None):
            return _validate_read(ref, raw, validate, document)
    with reads._lock:
        previous = validated_read(repository, ref, raw)
        if previous is not None:
            return previous
        preimage = reads._preimages.get((repository, ref)) if reuse_unchanged else None
        with reuse_variant_preimage(ref, preimage):
            record = _validate_read(ref, raw, validate, document)
        return remember_validated_read(repository, raw, record, full_schema=preimage is None) if remember else record


def decode_record_version(
    repository: object,
    ref: EntityRef,
    raw: bytes,
    *,
    validate: Callable[[dict[str, Any], EntityRef], PersistenceDto],
    reuse_unchanged: bool = False,
) -> RecordVersion:
    """Validate exact bytes as needed without decoding a previously checked payload."""
    version = validated_record_version(repository, ref, raw)
    if version is not None:
        return version
    record = decode_validated_read(repository, ref, raw, validate=validate, reuse_unchanged=reuse_unchanged)
    return RecordVersion.from_record(record)


def _validate_read(
    ref: EntityRef,
    raw: bytes,
    validate: Callable[[dict[str, Any], EntityRef], PersistenceDto],
    document: dict[str, Any] | None,
) -> LoadedRecord:
    value = validate(parse_json_bytes(raw) if document is None else document, ref)
    return LoadedRecord(ref, value, hashlib.sha256(raw).hexdigest())
