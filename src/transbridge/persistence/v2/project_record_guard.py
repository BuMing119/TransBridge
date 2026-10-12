"""Exact record preconditions shared by Project save and activation protocols."""

import hashlib

from transbridge.application.contracts import DomainError, ErrorCategory


def verified_preimage(filesystem, repository, ref, record):
    raw = filesystem.read_bytes(repository.path_for(ref))
    if hashlib.sha256(raw).hexdigest() != record.source_hash:
        raise DomainError(
            ErrorCategory.CONFLICT,
            "ACTIVE_SAVE_PREIMAGE_CHANGED",
            "A persistence record changed while its recovery preimage was captured.",
            retryable=True,
        )
    return raw


def require_record_version(record, expected_revision, expected_hash, *, identity):
    revision = None if record is None else record.value.envelope.revision
    if not record_matches(record, expected_revision, expected_hash):
        raise DomainError(
            ErrorCategory.CONFLICT,
            "PROJECT_ACTIVATION_STALE",
            "The persisted Project or Variant changed; reload and prepare the operation again.",
            retryable=True,
            details={"identity": identity, "expected_revision": expected_revision, "actual_revision": revision},
        )


def record_matches(record, expected_revision, expected_hash):
    revision = None if record is None else record.value.envelope.revision
    digest = None if record is None else record.source_hash
    return revision == expected_revision and (expected_hash is None or digest == expected_hash)
