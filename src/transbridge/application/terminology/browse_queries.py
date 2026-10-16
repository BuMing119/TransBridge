"""Full-collection terminology filtering over exact draft and effective snapshots."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from threading import RLock
from typing import TYPE_CHECKING, Protocol

from .browse_index import TermBrowseIndex
from .effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from .identity import canonical_digest, normalize_original
from .models import DecisionStatus, ScopeKind, TermDecision, TerminologyDraft
from .ports import Page, PageRequest
from .query_control import QueryCancellation

if TYPE_CHECKING:
    from transbridge.application.terminology_profiles.browse_queries import ProfileMappingRow


class TermBrowseStatus(StrEnum):
    EFFECTIVE = "effective"
    REVIEW_REQUIRED = "review_required"
    UNRESOLVED = "unresolved"
    EXCLUDED = "excluded"

    @classmethod
    def for_decision(cls, decision: TermDecision) -> TermBrowseStatus:
        if decision.suppressed:
            return cls.EXCLUDED
        if decision.is_effective:
            return cls.EFFECTIVE
        if decision.status is DecisionStatus.REVIEW_REQUIRED:
            return cls.REVIEW_REQUIRED
        return cls.UNRESOLVED


@dataclass(frozen=True, slots=True)
class TermFilter:
    search: str = ""
    scope: ScopeKind | None = None
    status: TermBrowseStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "search", normalize_original(self.search))
        if self.scope is not None:
            object.__setattr__(self, "scope", ScopeKind(self.scope))
        if self.status is not None:
            object.__setattr__(self, "status", TermBrowseStatus(self.status))

    def matches(self, decision: TermDecision) -> bool:
        return self.matches_normalized(
            decision, normalize_original(decision.original), normalize_original(decision.translation)
        )

    def matches_normalized(self, decision: TermDecision, original: str, translation: str) -> bool:
        if self.scope is not None and decision.scope.kind is not self.scope:
            return False
        if self.status is not None and TermBrowseStatus.for_decision(decision) is not self.status:
            return False
        return not self.search or self.search in original or self.search in translation


@dataclass(frozen=True, slots=True)
class TermBrowseQuery:
    project_id: str
    variant_id: str
    filters: TermFilter = TermFilter()

    def __post_init__(self) -> None:
        for name in ("project_id", "variant_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"terminology browse {name} must not be empty")
            object.__setattr__(self, name, value.strip())


@dataclass(frozen=True, slots=True)
class TermBrowseContext:
    effective_snapshot: EffectiveTerminologySnapshot
    snapshot_digest: str
    base_version_id: str | None = None
    profile_id: str | None = None
    profile_name: str | None = None
    profile_revision: int | None = None
    mapping_rows: tuple[ProfileMappingRow, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.snapshot_digest:
            raise ValueError("terminology browse context requires a snapshot digest")
        object.__setattr__(self, "mapping_rows", tuple(self.mapping_rows))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    @property
    def status(self) -> EffectiveSnapshotStatus:
        return self.effective_snapshot.status


class TerminologyBrowsePort(Protocol):
    def describe(
        self, query: TermBrowseQuery, *, cancellation: QueryCancellation | None = None
    ) -> TermBrowseContext: ...

    def list_effective_terms(
        self,
        query: TermBrowseQuery,
        request: PageRequest = PageRequest(),
        *,
        cancellation: QueryCancellation | None = None,
    ) -> Page[TermDecision]: ...

    def list_browsable_draft_terms(
        self,
        query: TermBrowseQuery,
        request: PageRequest = PageRequest(),
        *,
        cancellation: QueryCancellation | None = None,
    ) -> Page[TermDecision]: ...


class TermBrowseSnapshotPort(Protocol):
    def describe(
        self, project_id: str, variant_id: str, *, cancellation: QueryCancellation | None = None
    ) -> TermBrowseContext: ...


class DraftBrowsePort(Protocol):
    def active_draft(self, project_id: str, variant_id: str) -> TerminologyDraft | None: ...


class TerminologyBrowseUnavailableError(RuntimeError):
    """Distinguish unavailable or corrupt assets from an empty effective list."""


class TerminologyBrowseQueries:
    def __init__(self, drafts: DraftBrowsePort, snapshots: TermBrowseSnapshotPort) -> None:
        self._drafts = drafts
        self._snapshots = snapshots
        self._indexes: OrderedDict[tuple[str, str], TermBrowseIndex] = OrderedDict()
        self._draft_cache: tuple[object, TerminologyDraft | None] | None = None
        self._lock = RLock()

    def describe(self, query: TermBrowseQuery, *, cancellation: QueryCancellation | None = None) -> TermBrowseContext:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        context = self._snapshots.describe(query.project_id, query.variant_id, cancellation=cancellation)
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        snapshot = context.effective_snapshot
        if (snapshot.local_project_id, snapshot.local_variant_id) != (query.project_id, query.variant_id):
            raise ValueError("terminology browse snapshot belongs to another Project or Variant")
        return context

    def list_effective_terms(
        self,
        query: TermBrowseQuery,
        request: PageRequest = PageRequest(),
        *,
        cancellation: QueryCancellation | None = None,
    ) -> Page[TermDecision]:
        context = self.describe(query, cancellation=cancellation)
        if context.status in {EffectiveSnapshotStatus.UNAVAILABLE, EffectiveSnapshotStatus.CORRUPT}:
            detail = "; ".join(context.diagnostics) or context.status.value
            raise TerminologyBrowseUnavailableError(f"无法读取当前生效术语：{detail}")
        return self._page(
            context.effective_snapshot.decisions, context.snapshot_digest, "effective", query, request, cancellation
        )

    def list_browsable_draft_terms(
        self,
        query: TermBrowseQuery,
        request: PageRequest = PageRequest(),
        *,
        cancellation: QueryCancellation | None = None,
    ) -> Page[TermDecision]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        with self._lock:
            draft = self._read_draft(query, cancellation)
        if draft is not None and (draft.ref.project_id, draft.ref.variant_id) != (query.project_id, query.variant_id):
            raise ValueError("terminology draft belongs to another Project or Variant")
        identity = (query.project_id, query.variant_id, None if draft is None else draft.ref)
        digest = canonical_digest(identity, namespace="terminology.browse-draft.v1")
        return self._page(() if draft is None else draft.decisions, digest, "draft", query, request, cancellation)

    def _page(self, decisions, digest, kind, query, request, cancellation):
        with self._lock:
            key = (kind, digest)
            index = self._indexes.get(key)
            if index is None or (kind == "draft" and index.source is not decisions):
                index = TermBrowseIndex(decisions, cancellation, effective_only=kind == "effective")
                self._indexes[key] = index
                while len(self._indexes) > 2:
                    self._indexes.popitem(last=False)
            self._indexes.move_to_end(key)
            return index.page(digest, kind, query, request, cancellation)

    def _read_draft(self, query, cancellation):
        reference = getattr(self._drafts, "active_ref", None)
        token = getattr(self._drafts, "read_cache_token", None)
        if reference is None or token is None:
            return self._drafts.active_draft(query.project_id, query.variant_id)
        for _ in range(3):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            before = token()
            ref = reference(query.project_id, query.variant_id)
            key = (query.project_id, query.variant_id, ref, before)
            if self._draft_cache is not None and self._draft_cache[0] == key:
                draft = self._draft_cache[1]
            else:
                draft = self._drafts.active_draft(query.project_id, query.variant_id)
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if token() == before and (None if draft is None else draft.ref) == ref:
                self._draft_cache = (key, draft)
                return draft
        raise TerminologyBrowseUnavailableError("术语草稿在读取时发生变化，请重试")
