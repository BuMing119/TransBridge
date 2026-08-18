"""Immutable publication requests, evidence, and results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re

from transbridge.application.contracts import OperationOutcome
from transbridge.application.io.contracts import FormatId

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ConflictPolicy(StrEnum):
    FAIL = "fail"
    EXPLICIT_OVERWRITE = "explicit-overwrite"


class BackupPolicy(StrEnum):
    NONE = "none"
    IF_EXISTS = "if-exists"
    REQUIRED_IF_EXISTS = "required-if-exists"


class DebugArtifactPolicy(StrEnum):
    CLEAN = "clean"
    RETAIN_ON_FAILURE = "retain-on-failure"


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    exists: bool
    sha256: str | None = None
    size_bytes: int = 0
    mode: int | None = None

    def __post_init__(self) -> None:
        if self.size_bytes < 0:
            raise ValueError("file fingerprint size must not be negative")
        if self.exists:
            if self.sha256 is None or not _SHA256.fullmatch(self.sha256):
                raise ValueError("existing file fingerprint requires a SHA-256 digest")
        elif self.sha256 is not None or self.size_bytes or self.mode is not None:
            raise ValueError("missing file fingerprint cannot carry file metadata")

    @classmethod
    def missing(cls) -> FileFingerprint:
        return cls(False)


@dataclass(frozen=True, slots=True)
class PublishTarget:
    path: str
    conflict_policy: ConflictPolicy = ConflictPolicy.FAIL
    backup_policy: BackupPolicy = BackupPolicy.IF_EXISTS
    expected_fingerprint: FileFingerprint | None = None
    debug_policy: DebugArtifactPolicy = DebugArtifactPolicy.CLEAN

    def __post_init__(self) -> None:
        if not self.path or not self.path.strip():
            raise ValueError("publish target path must not be empty")


@dataclass(frozen=True, slots=True)
class StagedArtifact:
    path: str
    target_path: str
    initial_target_fingerprint: FileFingerprint
    run_id: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    format_id: FormatId
    structure_valid: bool
    reparse_valid: bool
    fidelity_valid: bool
    entry_count: int
    summary_sha256: str | None = None
    code: str = "VALID"
    message: str = "artifact validation completed"

    def __post_init__(self) -> None:
        if self.entry_count < 0:
            raise ValueError("validation entry count must not be negative")
        if self.summary_sha256 is not None and not _SHA256.fullmatch(self.summary_sha256):
            raise ValueError("validation summary must be a SHA-256 digest")
        expected = self.structure_valid and self.reparse_valid and self.fidelity_valid
        if self.valid != expected:
            raise ValueError("validation flags are inconsistent")
        if not self.code.strip() or not self.message.strip():
            raise ValueError("validation code and message must not be empty")


@dataclass(frozen=True, slots=True)
class PublishManifest:
    schema_version: int
    run_id: str
    format_id: FormatId
    target_path: str
    artifact_sha256: str
    size_bytes: int
    source_fingerprint: str | None
    previous_target_fingerprint: FileFingerprint
    validation_summary_sha256: str
    backup_path: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported publish manifest schema version")
        if not self.run_id.strip():
            raise ValueError("publish manifest run_id must not be empty")
        if not _SHA256.fullmatch(self.artifact_sha256):
            raise ValueError("publish manifest artifact hash is invalid")
        if not _SHA256.fullmatch(self.validation_summary_sha256):
            raise ValueError("publish manifest validation hash is invalid")
        if self.source_fingerprint is not None and not _SHA256.fullmatch(self.source_fingerprint):
            raise ValueError("publish manifest source hash is invalid")
        if self.size_bytes < 0:
            raise ValueError("publish manifest size must not be negative")


@dataclass(frozen=True, slots=True)
class PublishResult:
    outcome: OperationOutcome
    code: str
    message: str
    target_path: str
    published: bool = False
    manifest: PublishManifest | None = None
    validation: ValidationReport | None = None
    backup_path: str | None = None
    retained_staging_path: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("publish result code and message must not be empty")
        if self.outcome is OperationOutcome.COMPLETED:
            if not self.published or self.manifest is None or self.validation is None:
                raise ValueError("completed publish result requires committed validation evidence")
        elif self.outcome in {OperationOutcome.FAILED, OperationOutcome.CANCELLED}:
            if self.published or self.manifest is not None:
                raise ValueError("failed or cancelled publish result cannot claim a committed artifact")
        elif self.outcome is OperationOutcome.PARTIAL:
            if not self.published or self.manifest is None:
                raise ValueError("partial publish result must identify the committed artifact")
