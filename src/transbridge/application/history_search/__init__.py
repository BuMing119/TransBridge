"""Read-only search across persisted translations and terminology."""

from .models import (
    HistoryDiagnostic,
    HistoryEntryKind,
    HistoryQuery,
    HistorySearchHit,
    HistorySearchPage,
    HistorySearchScope,
    HistorySearchScopeKind,
    HistorySourceRef,
    HistorySourceType,
    IndexStatus,
    RefreshReport,
    SourceRecord,
    normalize_search_text,
)
from .service import HistorySearchProvider, HistorySearchRefreshService, HistorySearchService, ProviderResult
from .task_adapter import HistorySearchTaskEntrypoint

__all__ = [
    "HistoryDiagnostic",
    "HistoryEntryKind",
    "HistoryQuery",
    "HistorySearchHit",
    "HistorySearchPage",
    "HistorySearchScope",
    "HistorySearchScopeKind",
    "HistorySearchProvider",
    "HistorySearchRefreshService",
    "HistorySearchService",
    "HistorySearchTaskEntrypoint",
    "HistorySourceRef",
    "HistorySourceType",
    "IndexStatus",
    "ProviderResult",
    "RefreshReport",
    "SourceRecord",
    "normalize_search_text",
]
