"""Snapshot-bound quality-report queries shared by UI preview and renderers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import CursorStaleError
from .models import (
    BuildResult,
    BuildSummary,
    ConflictGroup,
    ManualAction,
    TermDecision,
    TerminologyReportSnapshot,
    TerminologyReportSnapshotManifest,
    TerminologyReportSnapshotRef,
)
from .ports import Page, PageRequest, SnapshotCursor


class ReportSnapshotReader(Protocol):
    def get_report_snapshot(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshot: ...

    def get_report_manifest(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshotManifest: ...

    def get_build(self, ref: Any) -> BuildResult: ...


@dataclass(frozen=True, slots=True)
class ReportSnapshotSummary:
    snapshot_ref: TerminologyReportSnapshotRef
    build_ref: Any
    build_summary: BuildSummary
    project_id: str
    variant_id: str
    draft_identity: str
    term_count: int
    conflict_count: int
    manual_action_count: int


class TerminologyReportQueryService:
    """Keep every query pinned to the exact digest carried by the snapshot ref."""

    def __init__(self, source: ReportSnapshotReader) -> None:
        self._source = source

    def summary(self, ref: TerminologyReportSnapshotRef) -> ReportSnapshotSummary:
        manifest = self._source.get_report_manifest(ref)
        build = self._source.get_build(manifest.build_ref)
        return ReportSnapshotSummary(
            manifest.snapshot_ref,
            manifest.build_ref,
            build.summary,
            build.project_id,
            build.variant_id,
            manifest.draft_identity,
            manifest.section_count("terms"),
            manifest.section_count("conflicts"),
            manifest.section_count("manual"),
        )

    def terms(self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()) -> Page[TermDecision]:
        return self._page(ref, request, "terms", "term_id", lambda item: item.terms)

    def conflicts(self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()) -> Page[ConflictGroup]:
        return self._page(ref, request, "conflicts", "conflict_group_id", lambda item: item.conflicts)

    def manual_actions(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ManualAction]:
        return self._page(ref, request, "manual", "action_id", lambda item: item.manual_actions)

    def iter_terms(self, ref: TerminologyReportSnapshotRef, *, page_size: int = 1000) -> Iterator[TermDecision]:
        yield from self._iterate(self.terms, ref, page_size)

    def iter_conflicts(self, ref: TerminologyReportSnapshotRef, *, page_size: int = 1000) -> Iterator[ConflictGroup]:
        yield from self._iterate(self.conflicts, ref, page_size)

    def iter_manual_actions(
        self, ref: TerminologyReportSnapshotRef, *, page_size: int = 1000
    ) -> Iterator[ManualAction]:
        yield from self._iterate(self.manual_actions, ref, page_size)

    def _page[T](
        self,
        ref: TerminologyReportSnapshotRef,
        request: PageRequest,
        section: str,
        id_attribute: str,
        values: Callable[[TerminologyReportSnapshot], tuple[T, ...]],
    ) -> Page[T]:
        method = getattr(self._source, f"list_report_{section}", None)
        if callable(method):
            return method(ref, request)
        snapshot = self._source.get_report_snapshot(ref)
        items = values(snapshot)
        cursor = request.cursor
        if cursor is not None and (
            cursor.snapshot_digest != ref.content_digest or cursor.query_fingerprint != request.query_fingerprint
        ):
            raise CursorStaleError("cursor does not belong to this report snapshot and query")
        if cursor is not None and cursor.sort_values != (cursor.stable_id,):
            raise CursorStaleError("cursor sort key does not match the stable report order")
        after = None if cursor is None else cursor.stable_id
        eligible = tuple(item for item in items if after is None or str(getattr(item, id_attribute)) > after)
        page_items = eligible[: request.limit]
        next_cursor = None
        if len(eligible) > len(page_items) and page_items:
            stable_id = str(getattr(page_items[-1], id_attribute))
            next_cursor = SnapshotCursor(ref.content_digest, request.query_fingerprint, (stable_id,), stable_id)
        return Page(page_items, ref.content_digest, next_cursor, len(items))

    @staticmethod
    def _iterate[T](
        query: Callable[[TerminologyReportSnapshotRef, PageRequest], Page[T]],
        ref: TerminologyReportSnapshotRef,
        page_size: int,
    ) -> Iterator[T]:
        request = PageRequest(limit=page_size)
        while True:
            page = query(ref, request)
            yield from page.items
            if page.next_cursor is None:
                return
            request = PageRequest(limit=page_size, cursor=page.next_cursor)


__all__ = ["ReportSnapshotReader", "ReportSnapshotSummary", "TerminologyReportQueryService"]
