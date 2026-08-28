"""Project-isolated SQLite persistence for terminology facts."""

from .artifacts import ArtifactLedger
from .cache import CacheKind, TerminologyCache
from .changelog import ChangelogDocumentStore
from .connection import (
    StorageMode,
    TerminologyConnectionFactory,
    TerminologyStorageError,
    TerminologyStorageFullError,
    TerminologyStorageReadOnlyError,
    TerminologyStorageState,
)
from .draft_transactions import DraftLineStateReader, SqliteDraftTransactionAdapter
from .effective import SqliteEffectiveTerminologySnapshotPort
from .migration import MigrationManifest, TerminologyMigrator
from .paths import TerminologyPaths
from .queries import CursorCodec, QueryFingerprint
from .report_snapshot import ReportSection, SqliteReportSnapshotStore
from .repository import SqliteTerminologyRepository, SqliteTerminologyTransaction

__all__ = [
    "ArtifactLedger",
    "CacheKind",
    "ChangelogDocumentStore",
    "CursorCodec",
    "DraftLineStateReader",
    "MigrationManifest",
    "QueryFingerprint",
    "ReportSection",
    "SqliteTerminologyRepository",
    "SqliteReportSnapshotStore",
    "SqliteDraftTransactionAdapter",
    "SqliteEffectiveTerminologySnapshotPort",
    "SqliteTerminologyTransaction",
    "StorageMode",
    "TerminologyCache",
    "TerminologyConnectionFactory",
    "TerminologyMigrator",
    "TerminologyPaths",
    "TerminologyStorageError",
    "TerminologyStorageFullError",
    "TerminologyStorageReadOnlyError",
    "TerminologyStorageState",
]
