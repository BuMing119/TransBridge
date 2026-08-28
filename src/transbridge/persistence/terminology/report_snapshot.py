"""Immutable report manifests and snapshot-bound paged section storage."""

from __future__ import annotations

from enum import StrEnum
import json
import sqlite3
from typing import Any, Protocol

from transbridge.application.terminology.errors import CursorStaleError, DigestCollisionError, TerminologyNotFoundError
from transbridge.application.terminology.models import (
    BuildResult,
    BuildResultRef,
    ConflictGroup,
    ManualAction,
    TermDecision,
    TerminologyReportSnapshot,
    TerminologyReportSnapshotManifest,
    TerminologyReportSnapshotRef,
)
from transbridge.application.terminology.ports import Page, PageRequest, SnapshotCursor
from transbridge.application.terminology.reports import build_report_manifest

from .codec import dumps, loads


class ReportSection(StrEnum):
    TERMS = "terms"
    CONFLICTS = "conflicts"
    MANUAL = "manual"


class BuildReader(Protocol):
    def get_build(self, ref: BuildResultRef) -> BuildResult: ...


class SqliteReportSnapshotStore:
    """Store one immutable manifest plus independently pageable frozen sections."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        build_reader: BuildReader | None = None,
    ) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row
        self._build_reader = build_reader

    def get_build(self, ref: BuildResultRef) -> BuildResult:
        if self._build_reader is None:
            raise RuntimeError("a BuildReader is required for report summary queries")
        return self._build_reader.get_build(ref)

    def put_report_snapshot(self, snapshot: TerminologyReportSnapshot) -> TerminologyReportSnapshotRef:
        manifest = build_report_manifest(snapshot)
        self._connection.execute("SAVEPOINT put_report_snapshot_sections")
        try:
            row = self._connection.execute(
                "SELECT payload_json FROM report_snapshots WHERE snapshot_id = ?",
                (snapshot.ref.snapshot_id,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO report_snapshots(snapshot_id, content_digest, build_key, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        snapshot.ref.snapshot_id,
                        snapshot.ref.content_digest,
                        snapshot.build_ref.build_key,
                        dumps(snapshot),
                    ),
                )
            elif loads(str(row["payload_json"]), TerminologyReportSnapshot) != snapshot:
                raise DigestCollisionError(snapshot.ref.snapshot_id)
            manifest_json = _manifest_json(manifest)
            existing = self._connection.execute(
                "SELECT manifest_json FROM report_snapshot_manifests WHERE snapshot_id = ?",
                (snapshot.ref.snapshot_id,),
            ).fetchone()
            if existing is not None and str(existing["manifest_json"]) != manifest_json:
                raise DigestCollisionError(snapshot.ref.snapshot_id)
            self._connection.execute(
                "INSERT OR IGNORE INTO report_snapshot_manifests(snapshot_id, manifest_json) VALUES (?, ?)",
                (snapshot.ref.snapshot_id, manifest_json),
            )
            self._insert_section(snapshot.ref.snapshot_id, ReportSection.TERMS, snapshot.terms, "term_id")
            self._insert_section(
                snapshot.ref.snapshot_id,
                ReportSection.CONFLICTS,
                snapshot.conflicts,
                "conflict_group_id",
            )
            self._insert_section(snapshot.ref.snapshot_id, ReportSection.MANUAL, snapshot.manual_actions, "action_id")
            self._connection.execute("RELEASE SAVEPOINT put_report_snapshot_sections")
        except Exception:
            self._connection.execute("ROLLBACK TO SAVEPOINT put_report_snapshot_sections")
            self._connection.execute("RELEASE SAVEPOINT put_report_snapshot_sections")
            raise
        return snapshot.ref

    def get_report_snapshot(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshot:
        row = self._connection.execute(
            "SELECT content_digest, payload_json FROM report_snapshots WHERE snapshot_id = ?",
            (ref.snapshot_id,),
        ).fetchone()
        if row is None or str(row["content_digest"]) != ref.content_digest:
            raise TerminologyNotFoundError("report snapshot was not found")
        return loads(str(row["payload_json"]), TerminologyReportSnapshot)

    def get_report_manifest(self, ref: TerminologyReportSnapshotRef) -> TerminologyReportSnapshotManifest:
        row = self._connection.execute(
            "SELECT s.content_digest, m.manifest_json FROM report_snapshots s "
            "JOIN report_snapshot_manifests m ON m.snapshot_id = s.snapshot_id WHERE s.snapshot_id = ?",
            (ref.snapshot_id,),
        ).fetchone()
        if row is None or str(row["content_digest"]) != ref.content_digest:
            raise TerminologyNotFoundError("report snapshot manifest was not found")
        return _load_manifest(str(row["manifest_json"]))

    def list_report_terms(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[TermDecision]:
        return self._page(ref, ReportSection.TERMS, request, TermDecision)

    def list_report_conflicts(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ConflictGroup]:
        return self._page(ref, ReportSection.CONFLICTS, request, ConflictGroup)

    def list_report_manual(
        self, ref: TerminologyReportSnapshotRef, request: PageRequest = PageRequest()
    ) -> Page[ManualAction]:
        return self._page(ref, ReportSection.MANUAL, request, ManualAction)

    def _insert_section(self, snapshot_id: str, section: ReportSection, items: tuple[Any, ...], id_attr: str) -> None:
        for item in items:
            stable_id = str(getattr(item, id_attr))
            payload = dumps(item)
            row = self._connection.execute(
                "SELECT payload_json FROM report_snapshot_sections "
                "WHERE snapshot_id = ? AND section = ? AND stable_id = ?",
                (snapshot_id, section.value, stable_id),
            ).fetchone()
            if row is not None and str(row["payload_json"]) != payload:
                raise DigestCollisionError(f"{snapshot_id}:{section.value}:{stable_id}")
            self._connection.execute(
                "INSERT OR IGNORE INTO report_snapshot_sections(snapshot_id, section, stable_id, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (snapshot_id, section.value, stable_id, payload),
            )

    def _page[T](
        self,
        ref: TerminologyReportSnapshotRef,
        section: ReportSection,
        request: PageRequest,
        expected_type: type[T],
    ) -> Page[T]:
        self._require_snapshot_ref(ref)
        cursor = request.cursor
        if cursor is not None and (
            cursor.snapshot_digest != ref.content_digest or cursor.query_fingerprint != request.query_fingerprint
        ):
            raise CursorStaleError("cursor does not belong to this report snapshot and query")
        if cursor is not None and cursor.sort_values != (cursor.stable_id,):
            raise CursorStaleError("cursor sort key does not match the stable report order")
        parameters: list[Any] = [ref.snapshot_id, section.value]
        after_clause = ""
        if cursor is not None:
            after_clause = " AND stable_id > ?"
            parameters.append(cursor.stable_id)
        rows = self._connection.execute(
            "SELECT stable_id, payload_json FROM report_snapshot_sections "
            f"WHERE snapshot_id = ? AND section = ?{after_clause} ORDER BY stable_id LIMIT ?",
            (*parameters, request.limit + 1),
        ).fetchall()
        total = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM report_snapshot_sections WHERE snapshot_id = ? AND section = ?",
                (ref.snapshot_id, section.value),
            ).fetchone()[0]
        )
        visible = rows[: request.limit]
        next_cursor = None
        if len(rows) > request.limit and visible:
            stable_id = str(visible[-1]["stable_id"])
            next_cursor = SnapshotCursor(ref.content_digest, request.query_fingerprint, (stable_id,), stable_id)
        return Page(
            tuple(loads(str(row["payload_json"]), expected_type) for row in visible),
            ref.content_digest,
            next_cursor,
            total,
        )

    def _require_snapshot_ref(self, ref: TerminologyReportSnapshotRef) -> None:
        row = self._connection.execute(
            "SELECT content_digest FROM report_snapshots WHERE snapshot_id = ?",
            (ref.snapshot_id,),
        ).fetchone()
        if row is None or str(row["content_digest"]) != ref.content_digest:
            raise TerminologyNotFoundError("report snapshot was not found")


def _manifest_json(manifest: TerminologyReportSnapshotManifest) -> str:
    return json.dumps(
        {
            "snapshot_id": manifest.snapshot_ref.snapshot_id,
            "snapshot_digest": manifest.snapshot_ref.content_digest,
            "build_key": manifest.build_ref.build_key,
            "build_digest": manifest.build_ref.content_digest,
            "draft_identity": manifest.draft_identity,
            "section_digests": dict(manifest.section_digests),
            "section_counts": dict(manifest.section_counts),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _load_manifest(payload: str) -> TerminologyReportSnapshotManifest:
    value = json.loads(payload)
    return TerminologyReportSnapshotManifest(
        TerminologyReportSnapshotRef(str(value["snapshot_id"]), str(value["snapshot_digest"])),
        BuildResultRef(str(value["build_key"]), str(value["build_digest"])),
        str(value["draft_identity"]),
        tuple((str(key), str(item)) for key, item in value["section_digests"].items()),
        tuple((str(key), int(item)) for key, item in value["section_counts"].items()),
    )


__all__ = ["ReportSection", "SqliteReportSnapshotStore"]
