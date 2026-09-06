"""Application service that rebuilds the derived history-search projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .models import (
    HistoryDiagnostic,
    HistoryQuery,
    HistorySearchPage,
    HistorySearchScope,
    IndexStatus,
    RefreshReport,
    SourceRecord,
)


@dataclass(frozen=True, slots=True)
class ProviderResult:
    records: tuple[SourceRecord, ...] = ()
    diagnostics: tuple[HistoryDiagnostic, ...] = ()


class HistorySearchProvider(Protocol):
    name: str

    def collect(self, cancellation) -> ProviderResult: ...


class HistorySearchIndexPort(Protocol):
    def replace(
        self,
        records: tuple[SourceRecord, ...],
        diagnostics: tuple[HistoryDiagnostic, ...],
        *,
        built_at: str,
        cancellation=None,
    ) -> None: ...


class HistorySearchQueryPort(Protocol):
    def query(self, request: HistoryQuery) -> HistorySearchPage: ...

    def scopes(self) -> tuple[HistorySearchScope, ...]: ...

    def status(self) -> IndexStatus: ...


class HistorySearchService:
    """Read-only application boundary used by desktop and future entrypoints."""

    def __init__(self, index: HistorySearchQueryPort) -> None:
        self._index = index

    def query(self, request: HistoryQuery) -> HistorySearchPage:
        return self._index.query(request)

    def scopes(self) -> tuple[HistorySearchScope, ...]:
        return self._index.scopes()

    def status(self) -> IndexStatus:
        return self._index.status()


class HistorySearchRefreshService:
    def __init__(self, index: HistorySearchIndexPort, providers: tuple[HistorySearchProvider, ...]) -> None:
        self._index = index
        self._providers = providers

    def refresh(self, cancellation=None) -> RefreshReport:
        records: list[SourceRecord] = []
        diagnostics: list[HistoryDiagnostic] = []
        for provider in self._providers:
            _raise_if_cancelled(cancellation)
            try:
                result = provider.collect(cancellation)
            except Exception as exc:  # noqa: BLE001 - one source family must not hide the others
                diagnostics.append(
                    HistoryDiagnostic(
                        "HISTORY_PROVIDER_FAILED",
                        f"{provider.name} 数据读取失败：{exc}",
                        provider.name,
                    )
                )
                continue
            records.extend(result.records)
            diagnostics.extend(result.diagnostics)
        _raise_if_cancelled(cancellation)
        built_at = datetime.now(UTC).isoformat()
        frozen_records = tuple(records)
        frozen_diagnostics = tuple(diagnostics)
        self._index.replace(
            frozen_records,
            frozen_diagnostics,
            built_at=built_at,
            cancellation=cancellation,
        )
        return RefreshReport(len(frozen_records), len(self._providers), built_at, frozen_diagnostics)


def _raise_if_cancelled(cancellation) -> None:
    if cancellation is None:
        return
    raise_method = getattr(cancellation, "raise_if_cancelled", None)
    if callable(raise_method):
        raise_method()
    elif getattr(cancellation, "is_cancelled", False):
        from transbridge.application.tasks import TaskCancelled

        raise TaskCancelled("history search refresh was cancelled")
