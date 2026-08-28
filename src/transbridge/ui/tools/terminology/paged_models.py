"""Bounded, snapshot-bound keyset table models for terminology projections."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
from typing import Any

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, QRunnable, Qt, QThreadPool, pyqtSignal

from transbridge.application.terminology import CursorStaleError, Page, PageRequest, SnapshotCursor


@dataclass(frozen=True, slots=True)
class PagedColumn:
    key: str
    label: str
    value: Callable[[object], object]


@dataclass(frozen=True, slots=True)
class PageQuery:
    snapshot_ref: object
    query_fingerprint: str = "all"

    def __post_init__(self) -> None:
        if not self.query_fingerprint.strip():
            raise ValueError("query fingerprint must not be empty")


class _QuerySignals(QObject):
    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(int, object)


class _QueryRunnable(QRunnable):
    def __init__(
        self,
        generation: int,
        load: Callable[[], Page[Any]],
        signals: _QuerySignals,
        cancelled: threading.Event,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._load = load
        self._signals = signals
        self._cancelled = cancelled

    def run(self) -> None:
        if self._cancelled.is_set():
            return
        try:
            page = self._load()
        except Exception as exc:  # noqa: BLE001 - query-port boundary
            if not self._cancelled.is_set():
                self._signals.failed.emit(self._generation, exc)
            return
        if not self._cancelled.is_set():
            self._signals.succeeded.emit(self._generation, page)


class KeysetPagedTableModel(QAbstractTableModel):
    """Keep only visible keyset pages and reject late query generations.

    The loader is an application query port adapter. It runs outside the Qt
    event loop and must return an immutable :class:`Page`. All model mutation
    remains in the owning Qt thread through queued signals.
    """

    page_loaded = pyqtSignal(int)
    query_failed = pyqtSignal(str)
    cursor_restarted = pyqtSignal()
    loading_changed = pyqtSignal(bool)

    def __init__(
        self,
        loader: Callable[[object, PageRequest], Page[Any]],
        columns: tuple[PagedColumn, ...],
        parent: QObject | None = None,
        *,
        page_size: int = 100,
        max_cached_pages: int = 3,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        if not columns:
            raise ValueError("paged model requires at least one column")
        if not 1 <= page_size <= 1000:
            raise ValueError("page size must be between 1 and 1000")
        if max_cached_pages < 1:
            raise ValueError("max cached pages must be positive")
        self._loader = loader
        self._columns = columns
        self._page_size = page_size
        self._max_cached_pages = max_cached_pages
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._signals = _QuerySignals()
        self._signals.succeeded.connect(self._accept_page, Qt.ConnectionType.QueuedConnection)
        self._signals.failed.connect(self._accept_error, Qt.ConnectionType.QueuedConnection)
        self._query: PageQuery | None = None
        self._generation = 0
        self._pages: list[tuple[object, ...]] = []
        self._items: list[object] = []
        self._snapshot_digest: str | None = None
        self._next_cursor: SnapshotCursor | None = None
        self._has_more = False
        self._loading = False
        self._closed = False
        self._last_error: str | None = None
        self._cancelled = threading.Event()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def cached_page_count(self) -> int:
        return len(self._pages)

    @property
    def snapshot_digest(self) -> str | None:
        return self._snapshot_digest

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def closed(self) -> bool:
        return self._closed

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object | None:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            value = self._columns[index.column()].value(self._items[index.row()])
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.UserRole:
            return self._items[index.row()]
        return None

    def headerData(  # noqa: N802 - Qt API
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self._columns[section].label
        return super().headerData(section, orientation, role)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_query(self, snapshot_ref: object, *, query_fingerprint: str = "all") -> int:
        self._require_open()
        self._generation += 1
        self._cancelled.set()
        self._cancelled = threading.Event()
        self._query = PageQuery(snapshot_ref, query_fingerprint)
        self._reset_rows()
        self._request(None)
        return self._generation

    def refresh(self) -> int:
        self._require_open()
        if self._query is None:
            raise RuntimeError("cannot refresh before a query is configured")
        return self.set_query(self._query.snapshot_ref, query_fingerprint=self._query.query_fingerprint)

    def clear(self) -> None:
        """Clear a consumed snapshot while keeping the model reusable."""

        self._require_open()
        self._generation += 1
        self._cancelled.set()
        self._cancelled = threading.Event()
        self._query = None
        self._set_loading(False)
        self._reset_rows()

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: N802 - Qt API
        return not parent.isValid() and not self._closed and not self._loading and self._has_more

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:  # noqa: N802 - Qt API
        if parent.isValid() or not self.canFetchMore(parent):
            return
        self._request(self._next_cursor)

    def accept_page(self, generation: int, page: Page[Any]) -> bool:
        """Deterministic test/adapter seam; late generations are discarded."""

        return self._apply_page(generation, page)

    def accept_error(self, generation: int, error: BaseException) -> bool:
        return self._apply_error(generation, error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._cancelled.set()
        self._set_loading(False)
        self._signals.succeeded.disconnect(self._accept_page)
        self._signals.failed.disconnect(self._accept_error)

    def _request(self, cursor: SnapshotCursor | None) -> None:
        if self._closed or self._loading or self._query is None:
            return
        generation = self._generation
        query = self._query
        request = PageRequest(self._page_size, cursor, query.query_fingerprint)
        self._last_error = None
        self._set_loading(True)
        runnable = _QueryRunnable(
            generation,
            lambda: self._loader(query.snapshot_ref, request),
            self._signals,
            self._cancelled,
        )
        self._pool.start(runnable)

    def _accept_page(self, generation: int, value: object) -> None:
        if not isinstance(value, Page):
            self._apply_error(generation, TypeError("query loader did not return a Page"))
            return
        self._apply_page(generation, value)

    def _apply_page(self, generation: int, page: Page[Any]) -> bool:
        if self._closed or generation != self._generation:
            return False
        if self._snapshot_digest is not None and page.snapshot_digest != self._snapshot_digest:
            return self._restart_for_stale_cursor()
        self._set_loading(False)
        self._snapshot_digest = page.snapshot_digest
        new_page = tuple(page.items)
        if new_page:
            start = len(self._items)
            self.beginInsertRows(QModelIndex(), start, start + len(new_page) - 1)
            self._pages.append(new_page)
            self._items.extend(new_page)
            self.endInsertRows()
        self._next_cursor = page.next_cursor
        self._has_more = page.next_cursor is not None
        self._trim_cache()
        self.page_loaded.emit(len(new_page))
        return True

    def _accept_error(self, generation: int, error: object) -> None:
        self._apply_error(generation, error if isinstance(error, BaseException) else RuntimeError(str(error)))

    def _apply_error(self, generation: int, error: BaseException) -> bool:
        if self._closed or generation != self._generation:
            return False
        if _is_cursor_stale(error) and self._next_cursor is not None:
            return self._restart_for_stale_cursor()
        self._set_loading(False)
        self._last_error = str(error)
        self.query_failed.emit(self._last_error)
        return True

    def _restart_for_stale_cursor(self) -> bool:
        if self._query is None:
            return False
        query = self._query
        self._generation += 1
        self._cancelled.set()
        self._cancelled = threading.Event()
        self._set_loading(False)
        self._reset_rows()
        self.cursor_restarted.emit()
        self._query = query
        self._request(None)
        return True

    def _trim_cache(self) -> None:
        while len(self._pages) > self._max_cached_pages:
            removed = self._pages.pop(0)
            self.beginRemoveRows(QModelIndex(), 0, len(removed) - 1)
            del self._items[: len(removed)]
            self.endRemoveRows()

    def _reset_rows(self) -> None:
        self.beginResetModel()
        self._pages.clear()
        self._items.clear()
        self._snapshot_digest = None
        self._next_cursor = None
        self._has_more = False
        self._last_error = None
        self.endResetModel()

    def _set_loading(self, value: bool) -> None:
        if self._loading == value:
            return
        self._loading = value
        self.loading_changed.emit(value)

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("paged model is closed")


def _is_cursor_stale(error: BaseException) -> bool:
    code = str(getattr(error, "code", "")).upper()
    return isinstance(error, CursorStaleError) or "CURSOR_STALE" in code or "CURSOR_STALE" in str(error).upper()


__all__ = ["KeysetPagedTableModel", "PageQuery", "PagedColumn"]
