# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Page mutable draft records without decoding the whole draft graph."""

from bisect import bisect_left
from collections import OrderedDict
import sqlite3

from transbridge.application.terminology.browse_queries import TerminologyBrowseUnavailableError
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.identity import canonical_digest, normalize_original
from transbridge.application.terminology.models import ScopeKind, TermDecision, TermScope
from transbridge.application.terminology.ports import Page, SnapshotCursor

from .codec import loads

_DECISIONS_PATH = '$.fields.decisions."$tuple"'


class DraftBrowsePages:
    """Two exact draft metadata indexes, with at most eight filters per index."""

    def __init__(self, repository):
        self.repository = repository
        self._cache = OrderedDict()

    def page(self, query, request, *, cancellation=None):
        repo = self.repository
        if query.project_id != repo.project_id:
            raise ValueError("draft browse belongs to another Project")
        with repo._lock:
            for _ in range(3):
                self._check(cancellation)
                token = repo.read_cache_token()
                ref = repo.active_ref(query.project_id, query.variant_id)
                digest = canonical_digest(
                    (query.project_id, query.variant_id, ref), namespace="terminology.browse-draft.v1"
                )
                key = (query.project_id, query.variant_id, ref, token)
                index = self._cache.get(key)
                try:
                    if index is None:
                        index = self._index(query, ref, cancellation)
                        self._check(cancellation)
                        self._cache[key] = index
                        while len(self._cache) > 2:
                            self._cache.popitem(last=False)
                    result = self._page(index, digest, token, query, request, cancellation)
                except sqlite3.OperationalError as exc:
                    if "malformed JSON" in str(exc):
                        repo._mark_corrupt("invalid draft browse payload", cause=exc)
                    raise
                if token == repo.read_cache_token() and ref == repo.active_ref(query.project_id, query.variant_id):
                    return result
        raise TerminologyBrowseUnavailableError("术语草稿在读取时发生变化，请重试")

    def _index(self, query, ref, cancellation):
        rows = []
        if ref is not None:
            cursor = self.repository._connection.execute(
                "SELECT entry.value, json_extract(entry.value,'$.fields.term_id'), "
                "json_extract(entry.value,'$.fields.original'), "
                "json_extract(entry.value,'$.fields.normalized_original'), "
                "json_extract(entry.value,'$.fields.translation'), "
                "json_extract(entry.value,'$.fields.scope.fields.kind.value'), "
                "json_extract(entry.value,'$.fields.scope.fields.plugin_id'), "
                "json_extract(entry.value,'$.fields.status.value'), "
                "json_extract(entry.value,'$.fields.suppressed') "
                "FROM drafts, json_each(drafts.payload_json, ?) AS entry "
                "WHERE drafts.project_id=? AND drafts.variant_id=?",
                (_DECISIONS_PATH, query.project_id, query.variant_id),
            )
            for raw, stable_id, original, normalized, translation, scope, plugin, status, suppressed in cursor:
                self._check(cancellation)
                scope_key = TermScope(ScopeKind(scope), plugin).canonical_key
                state = (
                    "excluded"
                    if suppressed
                    else "effective"
                    if status in {"adopted", "manual_confirmed"}
                    else "review_required"
                    if status == "review_required"
                    else "unresolved"
                )
                rows.append((
                    (normalized, scope_key, stable_id),
                    normalize_original(original),
                    normalize_original(translation),
                    scope,
                    state,
                    raw,
                ))
        rows.sort(key=lambda item: item[0])
        return tuple(rows), OrderedDict()

    def _page(self, index, digest, token, query, request, cancellation):
        rows, filters = index
        fingerprint = canonical_digest(
            ("draft", query, request.query_fingerprint, token), namespace="terminology.browse-query.v1"
        )
        cursor = request.cursor
        if cursor is not None and (cursor.snapshot_digest != digest or cursor.query_fingerprint != fingerprint):
            raise CursorStaleError("术语内容或筛选条件已变化，请刷新列表")
        f = query.filters
        matched = filters.get(f)
        if matched is None:
            selected = []
            for row in rows:
                self._check(cancellation)
                if f.scope is not None and row[3] != f.scope.value:
                    continue
                if f.status is not None and row[4] != f.status.value:
                    continue
                if f.search and f.search not in row[1] and f.search not in row[2]:
                    continue
                selected.append(row)
            matched = tuple(selected), tuple(row[0] for row in selected)
            filters[f] = matched
            while len(filters) > 8:
                filters.popitem(last=False)
        filters.move_to_end(f)
        selected, keys = matched
        offset = 0
        if cursor is not None:
            offset = bisect_left(keys, cursor.sort_values)
            if offset == len(keys) or keys[offset] != cursor.sort_values or cursor.stable_id != keys[offset][-1]:
                raise CursorStaleError("术语分页位置已失效，请刷新列表")
            offset += 1
        visible = selected[offset : offset + request.limit]
        items = []
        # Retain serialized rows from the metadata scan: paging must not parse the
        # entire draft document again, and only visible rows become dataclasses.
        for row in visible:
            self._check(cancellation)
            item = loads(row[5], TermDecision)
            if (item.project_id, item.variant_id) != (query.project_id, query.variant_id):
                raise ValueError("draft term belongs to another Project or Variant")
            if (item.normalized_original, item.scope.canonical_key, item.term_id) != row[0]:
                raise CursorStaleError("草稿记录已变化，请刷新列表")
            items.append(item)
        next_cursor = None
        if offset + len(visible) < len(selected):
            next_cursor = SnapshotCursor(digest, fingerprint, visible[-1][0], visible[-1][0][-1])
        self._check(cancellation)
        return Page(tuple(items), digest, next_cursor, len(selected))

    @staticmethod
    def _check(cancellation):
        if cancellation is not None:
            cancellation.raise_if_cancelled()
