# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Carry one opening's verified input through the normal candidate loader."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json

from transbridge.application.contracts import DomainError, ErrorCategory
from transbridge.application.projects.source_inputs import MissingProjectSourceInput

from .v2.models import LoadedRecord
from .v2.project_record_guard import RecordVersion
from .v2.repository import VariantRepository
from .v2.schema import serialize_document
from .v2.validated_reads import ValidatedReads, reuse_validated_reads
from .v2.variant import VariantSnapshot


@dataclass(frozen=True, slots=True)
class _MutableJsonEvidence:
    value: dict | list
    encoded: bytes

    def check(self):
        cause = None
        try:
            if _encode_metadata(self.value) == self.encoded:
                return
        except (ValueError, TypeError) as exc:
            cause = exc
        raise DomainError(
            ErrorCategory.CONFLICT,
            "PREPARED_PROJECT_INPUT_CHANGED",
            "准备结果中的来源或版本元数据已变化，请重新打开工程。",
            cause=cause,
        ) from cause


def _encode_metadata(value):
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()


def _mutable_snapshot_evidence(snapshot, source_baselines):
    """Capture only mutable JSON subtrees, without serializing the entry payload."""
    seen = set()
    evidence = []

    def capture(value):
        if isinstance(value, dict | list):
            if id(value) not in seen:
                seen.add(id(value))
                evidence.append(_MutableJsonEvidence(value, _encode_metadata(value)))
        elif isinstance(value, tuple):
            for item in value:
                capture(item)

    for entries in (snapshot.entries, *(baseline.entries for baseline in source_baselines)):
        for entry in entries:
            for owner in (*entry.provenance, *entry.external_refs):
                for _, value in owner.metadata:
                    capture(value)
    # VariantSnapshot freezes label-library objects/arrays recursively. Its
    # label_library_data() accessor returns detached JSON containers.
    return tuple(evidence)


@dataclass(frozen=True, slots=True)
class _RecordIdentity:
    repository: object
    ref: object
    digest: str
    migrated_document: bytes | None

    @classmethod
    def capture(cls, repository, record):
        # Only an old-schema read needs a canonical migration comparison. Do
        # not retain the mutable DTO returned by the repository as evidence.
        migrated = serialize_document(record.value.envelope.to_dict()) if record.migrated else None
        return cls(repository, record.ref, record.source_hash, migrated)

    def check(self, repository, ref, source_hash, migration_report=None):
        same_record = repository is self.repository and ref == self.ref
        if same_record and source_hash == self.digest:
            return
        if (
            same_record
            and self.migrated_document is not None
            and source_hash == hashlib.sha256(self.migrated_document).hexdigest()
            and (migration_report is None or migration_report.original_hash == self.digest)
        ):
            return
        raise DomainError(
            ErrorCategory.CONFLICT,
            "PREPARED_PROJECT_INPUT_CHANGED",
            "工程文件在准备后发生变化，请重新打开以读取最新内容。",
        )


@dataclass(frozen=True, slots=True)
class PreparedProjectRead:
    """Bounded to one prepare result; never a cross-open or global cache."""

    project: _RecordIdentity
    variant: _RecordIdentity
    snapshot: VariantSnapshot
    evidence: ValidatedReads
    bound_sources: bytes | None = None
    source_relations: bytes = b"[]"
    source_baselines: tuple = ()
    mutable_metadata: tuple[_MutableJsonEvidence, ...] = ()
    missing_inputs: tuple[MissingProjectSourceInput, ...] = ()
    hydrations: tuple = ()

    @classmethod
    def capture(
        cls,
        projects,
        project,
        variants,
        variant,
        snapshot,
        evidence,
        *,
        bound_sources=None,
        source_baselines=(),
        missing_inputs=(),
        hydrations=(),
    ):
        return cls(
            _RecordIdentity.capture(projects, project),
            _RecordIdentity.capture(variants, variant),
            snapshot,
            evidence,
            None if bound_sources is None else json.dumps(bound_sources, sort_keys=True, allow_nan=False).encode(),
            _encode_metadata(project.value.envelope.data.get("source_relations", ())),
            source_baselines,
            _mutable_snapshot_evidence(snapshot, source_baselines),
            tuple(missing_inputs),
            tuple(hydrations),
        )


_PREPARED: ContextVar[PreparedProjectRead | None] = ContextVar("prepared_project_read", default=None)


@contextmanager
def use_prepared_project(read: PreparedProjectRead):
    token = _PREPARED.set(read)
    try:
        with reuse_validated_reads(read.evidence):
            yield
    finally:
        _PREPARED.reset(token)


def verify_prepared_project(repository, record: LoadedRecord):
    prepared = _PREPARED.get()
    if prepared is not None:
        prepared.project.check(repository, record.ref, record.source_hash, record.migration_report)


def prepared_project_sources():
    prepared = _PREPARED.get()
    return None if prepared is None or prepared.bound_sources is None else tuple(json.loads(prepared.bound_sources))


def prepared_missing_inputs():
    prepared = _PREPARED.get()
    return None if prepared is None else prepared.missing_inputs


def prepared_source_hydrations():
    prepared = _PREPARED.get()
    return None if prepared is None else prepared.hydrations


def has_prepared_source_coverage(project, snapshot, baselines, hydrations, bound_sources):
    """Reuse complete coverage only for the same verified source inputs and entries."""
    prepared = _PREPARED.get()
    if (
        prepared is None
        or prepared.missing_inputs
        or snapshot is not prepared.snapshot
        or baselines is not prepared.source_baselines
        or hydrations is not prepared.hydrations
        or bound_sources is None
    ):
        return False
    data = project.envelope.data
    return tuple(data.get("sources", ())) == bound_sources and tuple(data.get("source_relations", ())) == tuple(
        json.loads(prepared.source_relations)
    )


def prepared_variant(repository, record: LoadedRecord) -> VariantSnapshot | None:
    prepared = _PREPARED.get()
    if prepared is None:
        return None
    prepared.variant.check(repository, record.ref, record.source_hash, record.migration_report)
    return _checked_snapshot(prepared)


def reuse_prepared_variant(repository, ref) -> tuple[VariantSnapshot, RecordVersion] | None:
    """Match a current-schema candidate against fresh disk bytes under the read lease."""
    prepared = _PREPARED.get()
    if prepared is None or prepared.variant.migrated_document is not None:
        return None
    # Other load-port implementations retain their normal load behavior.
    if type(repository) is not VariantRepository:
        return None
    version = repository._read_validated_version(ref)
    if version is None:
        return None
    prepared.variant.check(repository, ref, version.source_hash)
    return _checked_snapshot(prepared), version


def _checked_snapshot(prepared: PreparedProjectRead) -> VariantSnapshot:
    for metadata in prepared.mutable_metadata:
        metadata.check()
    return prepared.snapshot
