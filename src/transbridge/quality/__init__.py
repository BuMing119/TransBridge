"""Quality evidence primitives used by local and CI QA entry points."""

from .evidence import (
    DEFAULT_ENV_ALLOWLIST,
    MANIFEST_ID,
    SCHEMA_VERSION,
    EvidenceValidationError,
    RunOutcome,
    capture_allowed_environment,
    replay_manifest,
    run_with_evidence,
    validate_manifest,
)

__all__ = [
    "DEFAULT_ENV_ALLOWLIST",
    "MANIFEST_ID",
    "SCHEMA_VERSION",
    "EvidenceValidationError",
    "RunOutcome",
    "capture_allowed_environment",
    "replay_manifest",
    "run_with_evidence",
    "validate_manifest",
]
