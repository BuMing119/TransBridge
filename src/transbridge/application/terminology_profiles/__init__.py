"""Base-game localization terminology profiles."""

from .effective import (
    ProfiledEffectiveTerminologySnapshotPort,
    decode_profiled_version_id,
    encode_profiled_version_id,
    is_profiled_version_id,
)
from .importing import (
    TerminologyProfileImportConflict,
    TerminologyProfileImportConflictKind,
    TerminologyProfileImportError,
    TerminologyProfileImportPreview,
    TerminologyProfileImportResult,
    TerminologyProfileImportService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from .in_memory import InMemoryTerminologyProfileRepository
from .models import (
    ProfileEntryOverride,
    ProfileOccurrenceBinding,
    ProfileState,
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfile,
    TerminologyProfileContent,
    TerminologyProfileSelection,
    logical_term_key,
    normalize_term,
)
from .projection import ProjectedTranslation, ProjectionDiagnostic, TerminologyProfileProjector, source_contains
from .service import TerminologyProfileConflictError, TerminologyProfileError, TerminologyProfileService
from .write_projection import FrozenProfileWriteProjection, TerminologyProfileWriteProjectionSource

__all__ = [
    "ProfileEntryOverride",
    "ProfileOccurrenceBinding",
    "ProfileState",
    "ProfileTermMapping",
    "ProfiledEffectiveTerminologySnapshotPort",
    "InMemoryTerminologyProfileRepository",
    "ProjectedTranslation",
    "ProjectionDiagnostic",
    "PublishedTerminologyProfile",
    "FrozenProfileWriteProjection",
    "TerminologyProfile",
    "TerminologyProfileConflictError",
    "TerminologyProfileContent",
    "TerminologyProfileError",
    "TerminologyProfileImportConflict",
    "TerminologyProfileImportConflictKind",
    "TerminologyProfileImportError",
    "TerminologyProfileImportPreview",
    "TerminologyProfileImportResult",
    "TerminologyProfileImportService",
    "TerminologyProfileSelection",
    "TerminologyProfileService",
    "TerminologySourceEntry",
    "TerminologySourceSnapshot",
    "TerminologyProfileWriteProjectionSource",
    "TerminologyProfileProjector",
    "logical_term_key",
    "decode_profiled_version_id",
    "encode_profiled_version_id",
    "is_profiled_version_id",
    "normalize_term",
    "source_contains",
]
