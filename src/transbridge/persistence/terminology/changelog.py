"""Immutable changelog manifest and document-bound paged section storage."""

from __future__ import annotations

import sqlite3
from typing import Any

from transbridge.application.terminology.changelog_queries import (
    ChangeLogSection,
    build_changelog_manifest,
)
from transbridge.application.terminology.errors import (
    CursorStaleError,
    DigestCollisionError,
    TerminologyNotFoundError,
)
from transbridge.application.terminology.models import (
    CanonicalChange,
    ChangeLogDocument,
    ChangeLogDocumentManifest,
    ChangeLogDocumentRef,
)
from transbridge.application.terminology.ports import Page, PageRequest, SnapshotCursor

from .codec import dumps, loads


class ChangelogDocumentStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def put(self, document: ChangeLogDocument, *, version_key: str) -> ChangeLogDocumentRef:
        manifest = build_changelog_manifest(document)
        row = self._connection.execute(
            "SELECT payload_json FROM changelog_documents WHERE document_id = ?",
            (document.ref.document_id,),
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO changelog_documents(document_id, content_digest, version_key, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (document.ref.document_id, document.ref.content_digest, version_key, dumps(document)),
            )
        elif loads(str(row["payload_json"]), ChangeLogDocument) != document:
            raise DigestCollisionError(document.ref.document_id)
        self._put_manifest(manifest)
        self._put_sections(document)
        return document.ref

    def get(self, ref: ChangeLogDocumentRef) -> ChangeLogDocument:
        row = self._connection.execute(
            "SELECT content_digest, payload_json FROM changelog_documents WHERE document_id = ?",
            (ref.document_id,),
        ).fetchone()
        if row is None or str(row["content_digest"]) != ref.content_digest:
            raise TerminologyNotFoundError("changelog document was not found")
        return loads(str(row["payload_json"]), ChangeLogDocument)

    def get_changelog_manifest(self, ref: ChangeLogDocumentRef) -> ChangeLogDocumentManifest:
        row = self._connection.execute(
            "SELECT d.content_digest, m.payload_json FROM changelog_documents d "
            "JOIN changelog_manifests m ON m.document_id = d.document_id WHERE d.document_id = ?",
            (ref.document_id,),
        ).fetchone()
        if row is None or str(row["content_digest"]) != ref.content_digest:
            raise TerminologyNotFoundError("changelog manifest was not found")
        return loads(str(row["payload_json"]), ChangeLogDocumentManifest)

    def list_changelog_messages(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[tuple[str, tuple[str, ...]]]:
        return self._page(ref, ChangeLogSection.MESSAGES, request, tuple)

    def list_changelog_changes(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[CanonicalChange]:
        return self._page(ref, ChangeLogSection.CHANGES, request, CanonicalChange)

    def list_changelog_diagnostics(self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()) -> Page[str]:
        return self._page(ref, ChangeLogSection.DIAGNOSTICS, request, str)

    def list_changelog_conflict_group_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]:
        return self._page(ref, ChangeLogSection.CONFLICT_GROUP_IDS, request, str)

    def list_changelog_no_evidence_term_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]:
        return self._page(ref, ChangeLogSection.NO_EVIDENCE_TERM_IDS, request, str)

    def list_changelog_manual_action_ids(
        self, ref: ChangeLogDocumentRef, request: PageRequest = PageRequest()
    ) -> Page[str]:
        return self._page(ref, ChangeLogSection.MANUAL_ACTION_IDS, request, str)

    def _put_manifest(self, manifest: ChangeLogDocumentManifest) -> None:
        payload = dumps(manifest)
        row = self._connection.execute(
            "SELECT payload_json FROM changelog_manifests WHERE document_id = ?",
            (manifest.ref.document_id,),
        ).fetchone()
        if row is not None and str(row["payload_json"]) != payload:
            raise DigestCollisionError(manifest.ref.document_id)
        self._connection.execute(
            "INSERT OR IGNORE INTO changelog_manifests(document_id, payload_json) VALUES (?, ?)",
            (manifest.ref.document_id, payload),
        )

    def _put_sections(self, document: ChangeLogDocument) -> None:
        sections: tuple[tuple[ChangeLogSection, tuple[Any, ...], str | None], ...] = (
            (ChangeLogSection.MESSAGES, document.user_messages, None),
            (ChangeLogSection.CHANGES, document.changes, "change_id"),
            (ChangeLogSection.DIAGNOSTICS, document.diagnostics, None),
            (ChangeLogSection.CONFLICT_GROUP_IDS, document.conflict_group_ids, "value"),
            (ChangeLogSection.NO_EVIDENCE_TERM_IDS, document.no_evidence_term_ids, "value"),
            (ChangeLogSection.MANUAL_ACTION_IDS, document.manual_action_ids, "value"),
        )
        for section, items, identity in sections:
            for index, item in enumerate(items):
                stable_id = _stable_id(item, index, identity)
                payload = dumps(item)
                row = self._connection.execute(
                    "SELECT payload_json FROM changelog_sections "
                    "WHERE document_id = ? AND section = ? AND stable_id = ?",
                    (document.ref.document_id, section.value, stable_id),
                ).fetchone()
                if row is not None and str(row["payload_json"]) != payload:
                    raise DigestCollisionError(f"{document.ref.document_id}:{section.value}:{stable_id}")
                self._connection.execute(
                    "INSERT OR IGNORE INTO changelog_sections(document_id, section, stable_id, payload_json) "
                    "VALUES (?, ?, ?, ?)",
                    (document.ref.document_id, section.value, stable_id, payload),
                )

    def _page[T](
        self,
        ref: ChangeLogDocumentRef,
        section: ChangeLogSection,
        request: PageRequest,
        expected_type: type[T],
    ) -> Page[T]:
        manifest = self.get_changelog_manifest(ref)
        cursor = request.cursor
        if cursor is not None and (
            cursor.snapshot_digest != ref.content_digest or cursor.query_fingerprint != request.query_fingerprint
        ):
            raise CursorStaleError("cursor does not belong to this changelog document and query")
        if cursor is not None and cursor.sort_values != (cursor.stable_id,):
            raise CursorStaleError("cursor sort key does not match the stable changelog order")
        parameters: list[Any] = [ref.document_id, section.value]
        after_clause = ""
        if cursor is not None:
            after_clause = " AND stable_id > ?"
            parameters.append(cursor.stable_id)
        rows = self._connection.execute(
            "SELECT stable_id, payload_json FROM changelog_sections "
            f"WHERE document_id = ? AND section = ?{after_clause} ORDER BY stable_id LIMIT ?",
            (*parameters, request.limit + 1),
        ).fetchall()
        visible = rows[: request.limit]
        next_cursor = None
        if len(rows) > request.limit and visible:
            stable_id = str(visible[-1]["stable_id"])
            next_cursor = SnapshotCursor(ref.content_digest, request.query_fingerprint, (stable_id,), stable_id)
        return Page(
            tuple(loads(str(row["payload_json"]), expected_type) for row in visible),
            ref.content_digest,
            next_cursor,
            manifest.section_count(section.value),
        )


def _stable_id(item: object, index: int, identity: str | None) -> str:
    if identity == "change_id":
        return str(getattr(item, identity))
    if identity == "value":
        return str(item)
    return f"{index:012d}"


__all__ = ["ChangelogDocumentStore"]
