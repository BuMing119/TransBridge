"""Qt-free cursor navigation with bounded page-content retention."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from transbridge.application.terminology import Page, SnapshotCursor


@dataclass(frozen=True, slots=True)
class PageAddress:
    cursor: SnapshotCursor | None
    offset: int


@dataclass(frozen=True, slots=True)
class PaginationState:
    first_row: int
    last_row: int
    total: int | None
    can_previous: bool
    can_next: bool
    can_first: bool
    has_query: bool
    loading: bool
    error: str | None
    notice: str | None


class KeysetPageWindow:
    """Retain cursor addresses independently of the bounded row-content cache."""

    def __init__(self, *, manual: bool, capacity: int) -> None:
        self.manual = manual
        self.capacity = capacity
        self.reset()

    def reset(self) -> None:
        self.addresses = [PageAddress(None, 0)]
        self.current_index = 0
        self.pending_index = 0
        self.cache: OrderedDict[int, Page[Any]] = OrderedDict()
        self.auto_pages: list[tuple[object, ...]] = []
        self.items: tuple[object, ...] = ()
        self.offset = 0
        self.total: int | None = None
        self.snapshot_digest: str | None = None
        self.next_cursor: SnapshotCursor | None = None

    @property
    def cached_page_count(self) -> int:
        return len(self.cache) if self.manual else len(self.auto_pages)

    @property
    def pending_cursor(self) -> SnapshotCursor | None:
        return self.addresses[self.pending_index].cursor

    def choose(self, direction: str) -> bool:
        if not self.manual:
            return False
        if direction == "next" and self.next_cursor is not None:
            self.pending_index = self.current_index + 1
        elif direction == "previous" and self.current_index > 0:
            self.pending_index = self.current_index - 1
        elif direction == "first" and self.current_index > 0:
            self.pending_index = 0
        else:
            return False
        return True

    def prepare_refresh(self, *, retry_failed: bool) -> None:
        self.cache.clear()
        if not retry_failed:
            self.pending_index = self.current_index

    def accept(self, page: Page[Any], *, trim: bool = True) -> None:
        self.snapshot_digest = page.snapshot_digest
        self.total = page.total
        self.next_cursor = page.next_cursor
        if not self.manual:
            if page.items:
                self.auto_pages.append(tuple(page.items))
            if trim:
                self.trim_auto()
            self.items = tuple(item for cached in self.auto_pages for item in cached)
            return
        self.current_index = self.pending_index
        self.offset = self.addresses[self.current_index].offset
        self.items = tuple(page.items)
        self.cache[self.current_index] = page
        self.cache.move_to_end(self.current_index)
        while len(self.cache) > self.capacity:
            self.cache.popitem(last=False)
        if page.next_cursor is not None:
            address = PageAddress(page.next_cursor, self.offset + len(page.items))
            following = self.current_index + 1
            if following == len(self.addresses):
                self.addresses.append(address)
            elif self.addresses[following] != address:
                self.addresses[following:] = [address]
                self.cache = OrderedDict((key, value) for key, value in self.cache.items() if key <= self.current_index)

    def trim_auto(self) -> None:
        while len(self.auto_pages) > self.capacity:
            self.offset += len(self.auto_pages.pop(0))
        self.items = tuple(item for cached in self.auto_pages for item in cached)


__all__ = ["KeysetPageWindow", "PaginationState"]
