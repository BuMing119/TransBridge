"""Snapshot-bound Qt table adapter with explicit or legacy automatic paging."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from weakref import finalize

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt, QThreadPool, pyqtSignal

from transbridge.application.terminology import CursorStaleError, Page, PageRequest, SnapshotCursor

from .page_window import KeysetPageWindow, PaginationState
from .paged_dispatch import PageQueryDispatch


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


class KeysetPagedTableModel(QAbstractTableModel):
    """Apply query results on the owning Qt thread; retain only bounded row data."""

    page_loaded = pyqtSignal(int)
    query_failed = pyqtSignal(str)
    cursor_restarted = pyqtSignal()
    loading_changed = pyqtSignal(bool)
    pagination_changed = pyqtSignal()

    def __init__(
        self,
        loader: Callable[[object, PageRequest], Page[Any]],
        columns: tuple[PagedColumn, ...],
        parent: QObject | None = None,
        *,
        page_size: int = 100,
        max_cached_pages: int = 3,
        thread_pool: QThreadPool | None = None,
        manual_paging: bool = False,
        read_loader: Callable | None = None,
    ) -> None:
        super().__init__(parent)
        if not columns:
            raise ValueError("paged model requires at least one column")
        if not 1 <= page_size <= 1000:
            raise ValueError("page size must be between 1 and 1000")
        if max_cached_pages < 1:
            raise ValueError("max cached pages must be positive")
        self._loader, self._columns, self._page_size = loader, columns, page_size
        self._read_loader = read_loader
        self._window = KeysetPageWindow(manual=manual_paging, capacity=max_cached_pages)
        self._dispatch = PageQueryDispatch(thread_pool or QThreadPool.globalInstance())
        self._dispatch.signals.succeeded.connect(self.accept_page, Qt.ConnectionType.QueuedConnection)
        self._dispatch.signals.failed.connect(self.accept_error, Qt.ConnectionType.QueuedConnection)
        self.destroyed.connect(finalize(self, self._dispatch.close))
        self._query: PageQuery | None = None
        self._generation = 0
        self._loading = False
        self._last_error: str | None = None
        self._notice: str | None = None
        self._pending_cursor: SnapshotCursor | None = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def cached_page_count(self) -> int:
        return self._window.cached_page_count

    @property
    def snapshot_digest(self) -> str | None:
        return self._window.snapshot_digest

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def closed(self) -> bool:
        return self._dispatch.closed

    @property
    def pagination(self) -> PaginationState:
        window = self._window
        ready = not self.closed and not self._loading
        has_previous = window.manual and window.current_index > 0
        return PaginationState(
            window.offset + 1 if window.items else 0,
            window.offset + len(window.items) if window.items else 0,
            window.total,
            has_previous and ready,
            window.next_cursor is not None and ready,
            has_previous and ready,
            self._query is not None and not self.closed,
            self._loading,
            self._last_error,
            self._notice,
        )

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._window.items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object | None:
        if not index.isValid() or not 0 <= index.row() < len(self._window.items):
            return None
        if role in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole}:
            value = self._columns[index.column()].value(self._window.items[index.row()])
            return "" if value is None else str(value)
        if role == Qt.ItemDataRole.UserRole:
            return self._window.items[index.row()]
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
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable if index.isValid() else Qt.ItemFlag.NoItemFlags

    def set_query(self, snapshot_ref: object, *, query_fingerprint: str = "all") -> int:
        self._require_open()
        query = PageQuery(snapshot_ref, query_fingerprint)
        self._advance_generation()
        self._query = query
        self._reset_rows()
        self._request()
        return self._generation

    def refresh(self) -> int:
        self._require_open()
        if self._query is None:
            raise RuntimeError("cannot refresh before a query is configured")
        if not self._window.manual:
            return self.set_query(self._query.snapshot_ref, query_fingerprint=self._query.query_fingerprint)
        self._window.prepare_refresh(retry_failed=self._last_error is not None)
        self._advance_generation()
        self._request()
        return self._generation

    def clear(self) -> None:
        self._require_open()
        self._advance_generation()
        self._query = None
        self._reset_rows()
        self.pagination_changed.emit()

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:  # noqa: N802 - Qt API
        return (
            not self._window.manual
            and not parent.isValid()
            and not self.closed
            and not self._loading
            and self._window.next_cursor is not None
        )

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:  # noqa: N802 - Qt API
        if self.canFetchMore(parent):
            self._request(self._window.next_cursor)

    def navigate_next(self) -> None:
        self._request(direction="next") if self._window.manual else self.fetchMore()

    def navigate_previous(self) -> None:
        self._request(direction="previous")

    def navigate_first(self) -> None:
        self._request(direction="first")

    def accept_page(self, generation: int, page: object) -> bool:
        """Deterministic test/adapter seam; late query generations are discarded."""
        if self.closed or generation != self._generation:
            return False
        if not isinstance(page, Page):
            return self.accept_error(generation, TypeError("query loader did not return a Page"))
        if self.snapshot_digest is not None and page.snapshot_digest != self.snapshot_digest:
            return self._restart_for_stale_cursor()
        if self._window.manual:
            self.beginResetModel()
            self._window.accept(page)
            self.endResetModel()
        else:
            if page.items:
                start = self.rowCount()
                self.beginInsertRows(QModelIndex(), start, start + len(page.items) - 1)
            self._window.accept(page, trim=False)
            if page.items:
                self.endInsertRows()
            discarded = max(0, len(self._window.auto_pages) - self._window.capacity)
            removed_rows = sum(len(items) for items in self._window.auto_pages[:discarded])
            if removed_rows:
                self.beginRemoveRows(QModelIndex(), 0, removed_rows - 1)
                self._window.trim_auto()
                self.endRemoveRows()
        self._last_error = None
        self._set_loading(False)
        self.page_loaded.emit(len(page.items))
        self.pagination_changed.emit()
        return True

    def accept_error(self, generation: int, error: object) -> bool:
        if self.closed or generation != self._generation:
            return False
        stale = isinstance(error, CursorStaleError) or str(getattr(error, "code", "")).upper() == "CURSOR_STALE"
        # Automatic mode also keeps the historical public accept_error seam.
        has_cursor = self._pending_cursor is not None or (
            not self._window.manual and self._window.next_cursor is not None
        )
        if stale and has_cursor:
            return self._restart_for_stale_cursor()
        self._last_error = str(error)
        self._set_loading(False)
        self.query_failed.emit(self._last_error)
        self.pagination_changed.emit()
        return True

    def close(self) -> None:
        if self.closed:
            return
        self._generation += 1
        self._dispatch.close()
        self._set_loading(False)
        self._dispatch.signals.succeeded.disconnect(self.accept_page)
        self._dispatch.signals.failed.disconnect(self.accept_error)
        self.pagination_changed.emit()

    def _request(self, cursor: SnapshotCursor | None = None, *, direction: str | None = None) -> None:
        if self.closed or self._loading or self._query is None:
            return
        if direction is not None:
            if not self._window.choose(direction):
                return
            self._advance_generation()
        if self._window.manual:
            cursor = self._window.pending_cursor
        self._pending_cursor = cursor
        generation, query = self._generation, self._query
        request = PageRequest(self._page_size, cursor, query.query_fingerprint)
        self._last_error = None
        self._set_loading(True)
        if direction is not None and (cached := self._window.cache.get(self._window.pending_index)) is not None:
            self.accept_page(generation, cached)
            return
        if self._read_loader is None:
            self._dispatch.start(generation, lambda: self._loader(query.snapshot_ref, request))
        else:
            self._dispatch.start_read(generation, lambda token: self._read_loader(query.snapshot_ref, request, token))

    def _restart_for_stale_cursor(self) -> bool:
        if self._query is None:
            return False
        self._advance_generation()
        self._reset_rows()
        self._notice = "列表内容已更新，原页位置已失效，已返回首页；筛选条件保持不变。"
        self.cursor_restarted.emit()
        self._request()
        return True

    def _reset_rows(self) -> None:
        self.beginResetModel()
        self._window.reset()
        self._pending_cursor = None
        self._last_error = None
        self._notice = None
        self.endResetModel()

    def _set_loading(self, value: bool) -> None:
        if self._loading != value:
            self._loading = value
            self.loading_changed.emit(value)
        self.pagination_changed.emit()

    def _advance_generation(self) -> None:
        self._generation += 1
        self._dispatch.cancel()
        self._set_loading(False)

    def _require_open(self) -> None:
        if self.closed:
            raise RuntimeError("paged model is closed")


__all__ = ["KeysetPagedTableModel", "PageQuery", "PagedColumn", "PaginationState"]
