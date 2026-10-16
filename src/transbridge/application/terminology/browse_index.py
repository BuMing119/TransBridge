"""Bounded, immutable term search projections with keyset pagination."""

from __future__ import annotations

from bisect import bisect_left
from collections import OrderedDict
from typing import TYPE_CHECKING

from .errors import CursorStaleError
from .identity import canonical_digest, normalize_original
from .models import TermDecision
from .ports import Page, PageRequest, SnapshotCursor
from .query_control import QueryCancellation

if TYPE_CHECKING:
    from .browse_queries import TermBrowseQuery, TermFilter


class TermBrowseIndex:
    """One exact snapshot, at most eight recent filter results; no content hashing."""

    def __init__(
        self,
        decisions: tuple[TermDecision, ...],
        cancellation: QueryCancellation | None = None,
        *,
        effective_only: bool = False,
    ):
        self.source = decisions
        projected = []
        for item in decisions:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if effective_only and not item.is_effective:
                continue
            key = (item.normalized_original, item.scope.canonical_key, item.term_id)
            projected.append((key, item, normalize_original(item.original), normalize_original(item.translation)))
        projected.sort(key=lambda row: row[0])
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        self._rows = tuple(projected)
        self._matches: OrderedDict[TermFilter, tuple[tuple, tuple]] = OrderedDict()

    def page(
        self,
        digest: str,
        kind: str,
        query: TermBrowseQuery,
        request: PageRequest,
        cancellation: QueryCancellation | None = None,
    ) -> Page[TermDecision]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        fingerprint = canonical_digest(
            (kind, query, request.query_fingerprint), namespace="terminology.browse-query.v1"
        )
        cursor = request.cursor
        if cursor is not None and (cursor.snapshot_digest != digest or cursor.query_fingerprint != fingerprint):
            raise CursorStaleError("术语内容或筛选条件已变化，请刷新列表")
        filters = query.filters
        selected = self._matches.get(filters)
        if selected is None:
            rows = []
            for row in self._rows:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                if filters.matches_normalized(row[1], row[2], row[3]):
                    rows.append(row)
            selected = tuple(rows), tuple(row[0] for row in rows)
            self._matches[filters] = selected
            while len(self._matches) > 8:
                self._matches.popitem(last=False)
        self._matches.move_to_end(filters)
        rows, keys = selected
        offset = 0
        if cursor is not None:
            offset = bisect_left(keys, cursor.sort_values)
            if offset == len(keys) or keys[offset] != cursor.sort_values or cursor.stable_id != keys[offset][-1]:
                raise CursorStaleError("术语分页位置已失效，请刷新列表")
            offset += 1
        visible = rows[offset : offset + request.limit]
        next_cursor = None
        if offset + len(visible) < len(rows):
            last = visible[-1]
            next_cursor = SnapshotCursor(digest, fingerprint, last[0], last[1].term_id)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        return Page(tuple(row[1] for row in visible), digest, next_cursor, len(rows))
