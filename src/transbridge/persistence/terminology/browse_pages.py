# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""UI paging over immutable row records without hydrating a complete version.

Translation consumers retain the verified whole-snapshot API. This read model
loads only search/sort metadata and decodes the visible decisions on demand.
"""

from bisect import bisect_left
from collections import OrderedDict
from dataclasses import replace
import json

from transbridge.application.terminology.browse_queries import TerminologyBrowseUnavailableError
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.errors import CursorStaleError
from transbridge.application.terminology.identity import canonical_digest, normalize_original, term_id
from transbridge.application.terminology.models import DecisionStatus, ScopeKind, TermDecision, TermScope
from transbridge.application.terminology.ports import Page, SnapshotCursor
from transbridge.application.terminology_profiles.effective import profiled_snapshot_identity

from .codec import loads
from .connection import StorageMode


class EffectiveBrowsePages:
    def __init__(self, repository):
        self.repository = repository
        self._cache = OrderedDict()

    def page(self, query, request, profile, cancellation=None):
        repository = self.repository
        if query.project_id != repository.project_id:
            raise ValueError("terminology browse belongs to another Project")
        if profile is not None and profile.project_id != query.project_id:
            raise ValueError("terminology profile belongs to another Project")
        with repository._lock:
            for _ in range(3):
                self._check(cancellation)
                if repository.storage_state.mode is StorageMode.READ_ONLY or not repository.storage_state.integrity_ok:
                    raise TerminologyBrowseUnavailableError("术语数据库当前不可安全读取")
                token = repository.read_cache_token()
                row = repository._connection.execute(
                    "SELECT e.version_key, v.version_id, v.content_digest FROM effective_versions e "
                    "LEFT JOIN versions v ON v.version_key=e.version_key "
                    "AND v.project_id=e.project_id AND v.variant_id=e.variant_id "
                    "WHERE e.project_id=? AND e.variant_id=?",
                    (query.project_id, query.variant_id),
                ).fetchone()
                if row is not None and (row[1] is None or row[2] is None):
                    raise TerminologyBrowseUnavailableError("当前术语版本引用已失效")
                version_key = None if row is None else row[0]
                identity = None if row is None else (row[1], row[2])
                profile_ref = (
                    None if profile is None else (profile.profile_id, profile.revision, profile.content_digest)
                )
                key = (query.project_id, query.variant_id, identity, profile_ref, token)
                index = self._cache.get(key)
                if index is None:
                    index = self._index(query, version_key, identity, profile, cancellation)
                    self._check(cancellation)
                    self._cache[key] = index
                    while len(self._cache) > 2:
                        self._cache.popitem(last=False)
                result = self._page(index, query, request, version_key, token, cancellation)
                if token == repository.read_cache_token():
                    return result
        raise TerminologyBrowseUnavailableError("术语在读取时发生变化，请重试")

    def _index(self, query, version_key, identity, profile, cancellation):
        base = EffectiveTerminologySnapshot(
            query.project_id,
            query.variant_id,
            EffectiveSnapshotStatus.NO_PROJECT_VERSION if identity is None else EffectiveSnapshotStatus.READY,
            version_id=None if identity is None else identity[0],
            content_digest=None if identity is None else identity[1],
        )
        if profile is not None:
            version_id, digest = profiled_snapshot_identity(base, profile)
            base = replace(base, status=EffectiveSnapshotStatus.READY, version_id=version_id, content_digest=digest)
        digest = canonical_digest(
            (
                base.snapshot_identity,
                None if profile is None else (profile.profile_id, profile.revision, profile.content_digest),
            ),
            namespace="terminology.browse-effective.v1",
        )
        mappings = {}
        for mapping in () if profile is None else profile.content.mappings:
            self._check(cancellation)
            mappings[(normalize_original(mapping.original), mapping.scope_kind, mapping.plugin_id)] = mapping
        seen = set()
        rows = []
        cursor = self.repository._connection.execute(
            "SELECT stable_id, json_extract(payload_json,'$.fields.original'), "
            "json_extract(payload_json,'$.fields.normalized_original'), "
            "json_extract(payload_json,'$.fields.translation'), "
            "json_extract(payload_json,'$.fields.scope.fields.kind.value'), "
            "json_extract(payload_json,'$.fields.scope.fields.plugin_id'), "
            "json_extract(payload_json,'$.fields.status.value'), "
            "json_extract(payload_json,'$.fields.suppressed') FROM version_terms WHERE version_key=?",
            (version_key,),
        )
        for stable_id, original, normalized, translation, scope, plugin, status, suppressed in cursor:
            self._check(cancellation)
            match = (normalize_original(original), scope, plugin)
            seen.add(match)
            mapping = mappings.get(match)
            if suppressed or status not in {"adopted", "manual_confirmed"} or (profile is not None and mapping is None):
                continue
            translation = translation if mapping is None else mapping.translation
            scope_key = TermScope(ScopeKind(scope), plugin).canonical_key
            rows.append((
                (normalized, scope_key, stable_id),
                match[0],
                normalize_original(translation),
                scope,
                translation,
                None,
            ))
        for key, mapping in mappings.items():
            self._check(cancellation)
            if key in seen:
                continue
            scope = TermScope(ScopeKind(mapping.scope_kind), mapping.plugin_id)
            stable_id = term_id(
                project_id=query.project_id, variant_id=query.variant_id, original=mapping.original, scope=scope
            )
            rows.append((
                (key[0], scope.canonical_key, stable_id),
                key[0],
                normalize_original(mapping.translation),
                key[1],
                mapping.translation,
                mapping,
            ))
        rows.sort(key=lambda item: item[0])
        return digest, tuple(rows), OrderedDict()

    def _page(self, index, query, request, version_key, token, cancellation):
        digest, rows, filters = index
        fingerprint = canonical_digest(
            ("effective", query, request.query_fingerprint, token), namespace="terminology.browse-query.v1"
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
                if f.status is not None and f.status.value != "effective":
                    continue
                if f.scope is not None and row[3] != f.scope.value:
                    continue
                if f.search and f.search not in row[1] and f.search not in row[2]:
                    continue
                selected.append(row)
            matched = tuple(selected), tuple(r[0] for r in selected)
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
        identities = [row[0][-1] for row in visible if row[-1] is None]
        payloads = (
            {
                row[0]: row[1]
                for row in self.repository._connection.execute(
                    "SELECT stable_id, payload_json FROM version_terms WHERE version_key=? "
                    "AND stable_id IN (SELECT value FROM json_each(?))",
                    (version_key, json.dumps(identities)),
                )
            }
            if identities
            else {}
        )
        items = []
        for key, _original, _translation, _scope, translation, mapping in visible:
            self._check(cancellation)
            if mapping is None:
                raw = payloads.get(key[-1])
                if raw is None:
                    raise CursorStaleError("术语记录已变化，请刷新列表")
                decision = loads(raw, TermDecision)
                if (decision.project_id, decision.variant_id, decision.term_id) != (
                    query.project_id,
                    query.variant_id,
                    key[-1],
                ):
                    raise ValueError("term row belongs to another snapshot")
                if not decision.is_effective or (decision.normalized_original, decision.scope.canonical_key) != key[:2]:
                    raise ValueError("term row metadata is inconsistent")
                decision = replace(decision, translation=translation)
            else:
                decision = TermDecision(
                    key[-1],
                    query.project_id,
                    query.variant_id,
                    mapping.original,
                    key[0],
                    translation,
                    TermScope(ScopeKind(mapping.scope_kind), mapping.plugin_id),
                    DecisionStatus.ADOPTED,
                )
            items.append(decision)
        next_cursor = None
        if offset + len(visible) < len(selected):
            next_cursor = SnapshotCursor(digest, fingerprint, visible[-1][0], visible[-1][0][-1])
        self._check(cancellation)
        return Page(tuple(items), digest, next_cursor, len(selected))

    @staticmethod
    def _check(cancellation):
        if cancellation is not None:
            cancellation.raise_if_cancelled()
