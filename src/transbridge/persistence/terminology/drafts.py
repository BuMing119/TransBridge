"""Revisioned draft storage kept separate from immutable repository facts."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
from typing import Any

from transbridge.application.terminology.errors import (
    ActiveDraftError,
    CursorStaleError,
    RepositoryConflictError,
    RevisionConflictError,
    TerminologyNotFoundError,
)
from transbridge.application.terminology.models import (
    DraftRef,
    ManualAction,
    TermDecision,
    TerminologyDraft,
    TerminologyVersionRef,
)
from transbridge.application.terminology.ports import Page, PageRequest, SnapshotCursor

from .codec import dumps
from .queries import keyset_page


class DraftStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        project_id: str,
        *,
        decode: Callable[[str, type[Any]], Any],
        effective_ref: Callable[[str, str], TerminologyVersionRef | None],
    ) -> None:
        self._connection = connection
        self._project_id = project_id
        self._decode = decode
        self._effective_ref = effective_ref

    def create(self, draft: TerminologyDraft, *, historical_base: bool = False) -> DraftRef:
        self._require_project(draft.ref.project_id)
        if draft.ref.revision != 0:
            raise RevisionConflictError(0, draft.ref.revision)
        if self._row(draft.ref.project_id, draft.ref.variant_id) is not None:
            raise ActiveDraftError("an active draft already exists for this Project/Variant")
        if historical_base:
            self._validate_historical_base(draft.ref)
        else:
            self._validate_base(draft.ref)
        self._insert(draft)
        return draft.ref

    def update(self, draft: TerminologyDraft, *, expected_revision: int) -> DraftRef:
        self._require_project(draft.ref.project_id)
        row = self._row(draft.ref.project_id, draft.ref.variant_id)
        actual = None if row is None else int(row["revision"])
        if row is None or actual != expected_revision:
            raise RevisionConflictError(expected_revision, actual)
        current = self._decode(str(row["payload_json"]), TerminologyDraft)
        if draft.ref.draft_id != current.ref.draft_id:
            raise ActiveDraftError("updating an active draft cannot replace its identity")
        if (
            draft.ref.base_version_id != current.ref.base_version_id
            or draft.ref.base_content_digest != current.ref.base_content_digest
        ):
            raise RepositoryConflictError("draft base cannot change during an ordinary update")
        if draft.ref.revision != expected_revision + 1:
            raise RevisionConflictError(expected_revision + 1, draft.ref.revision)
        appended_actions = _appended_actions(current.actions, draft.actions)
        cursor = self._connection.execute(
            "UPDATE drafts SET revision = ?, decision_set_digest = ?, payload_json = ? "
            "WHERE project_id = ? AND variant_id = ? AND revision = ?",
            (
                draft.ref.revision,
                draft.ref.decision_set_digest,
                dumps(draft),
                draft.ref.project_id,
                draft.ref.variant_id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RevisionConflictError(expected_revision, None)
        self._insert_actions(draft.ref.draft_id, appended_actions)
        return draft.ref

    def replace(self, previous: DraftRef, replacement: TerminologyDraft) -> DraftRef:
        self._require_project(previous.project_id)
        self._require_project(replacement.ref.project_id)
        row = self._row(previous.project_id, previous.variant_id)
        if row is None:
            raise TerminologyNotFoundError("active terminology draft was not found")
        current = self._decode(str(row["payload_json"]), TerminologyDraft)
        if current.ref != previous:
            raise RepositoryConflictError("replacement source is no longer the active draft")
        if (replacement.ref.project_id, replacement.ref.variant_id) != (previous.project_id, previous.variant_id):
            raise RepositoryConflictError("replacement draft must remain on the same Project/Variant line")
        if replacement.ref.draft_id == previous.draft_id or replacement.ref.revision != 0:
            raise RepositoryConflictError("replacement requires a fresh draft identity at revision zero")
        _appended_actions(current.actions, replacement.actions)
        self._validate_base(replacement.ref)
        self._connection.execute(
            "DELETE FROM drafts WHERE project_id = ? AND variant_id = ?",
            (previous.project_id, previous.variant_id),
        )
        self._insert(replacement)
        return replacement.ref

    def active(self, project_id: str, variant_id: str) -> TerminologyDraft | None:
        self._require_project(project_id)
        row = self._row(project_id, variant_id)
        return None if row is None else self._decode(str(row["payload_json"]), TerminologyDraft)

    def discard(self, project_id: str, variant_id: str, *, expected_revision: int) -> None:
        self._require_project(project_id)
        row = self._row(project_id, variant_id)
        actual = None if row is None else int(row["revision"])
        if row is None or actual != expected_revision:
            raise RevisionConflictError(expected_revision, actual)
        self._connection.execute(
            "DELETE FROM drafts WHERE project_id = ? AND variant_id = ?",
            (project_id, variant_id),
        )

    def list_actions(self, ref: DraftRef, request: PageRequest) -> Page[ManualAction]:
        self._require_project(ref.project_id)
        row = self._row(ref.project_id, ref.variant_id)
        if row is None or self._decode(str(row["payload_json"]), TerminologyDraft).ref != ref:
            raise TerminologyNotFoundError("draft is not active at the requested identity and revision")
        return keyset_page(
            self._connection,
            table="draft_actions",
            owner_column="draft_id",
            owner_value=ref.draft_id,
            snapshot_digest=ref.decision_set_digest,
            request=request,
            decode=lambda payload: self._decode(payload, ManualAction),
        )

    def list_terms(self, ref: DraftRef, request: PageRequest) -> Page[TermDecision]:
        """Page the authoritative active draft without exposing its SQLite payload."""

        self._require_project(ref.project_id)
        row = self._row(ref.project_id, ref.variant_id)
        if row is None:
            raise TerminologyNotFoundError("active terminology draft was not found")
        draft = self._decode(str(row["payload_json"]), TerminologyDraft)
        if draft.ref != ref:
            raise CursorStaleError("draft changed before the requested page was read")
        cursor = request.cursor
        if cursor is not None and (
            cursor.snapshot_digest != ref.decision_set_digest
            or cursor.query_fingerprint != request.query_fingerprint
            or cursor.sort_values != (cursor.stable_id,)
        ):
            raise CursorStaleError("cursor does not belong to this draft and query")
        after = None if cursor is None else cursor.stable_id
        eligible = tuple(item for item in draft.decisions if after is None or item.term_id > after)
        items = eligible[: request.limit]
        next_cursor = None
        if len(eligible) > len(items) and items:
            stable_id = items[-1].term_id
            next_cursor = SnapshotCursor(
                ref.decision_set_digest,
                request.query_fingerprint,
                (stable_id,),
                stable_id,
            )
        return Page(items, ref.decision_set_digest, next_cursor, len(draft.decisions))

    def _insert(self, draft: TerminologyDraft) -> None:
        ref = draft.ref
        self._connection.execute(
            "INSERT INTO drafts(project_id, variant_id, draft_id, base_version_id, base_content_digest, revision, "
            "decision_set_digest, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ref.project_id,
                ref.variant_id,
                ref.draft_id,
                ref.base_version_id,
                ref.base_content_digest,
                ref.revision,
                ref.decision_set_digest,
                dumps(draft),
            ),
        )
        self._insert_actions(draft.ref.draft_id, draft.actions)

    def _insert_actions(self, draft_id: str, actions: tuple[ManualAction, ...]) -> None:
        self._connection.executemany(
            "INSERT INTO draft_actions(draft_id, stable_id, payload_json) VALUES (?, ?, ?)",
            ((draft_id, item.action_id, dumps(item)) for item in actions),
        )

    def _validate_historical_base(self, ref: DraftRef) -> None:
        if ref.base_version_id is None:
            raise RepositoryConflictError("historical draft requires an immutable base version")
        row = self._connection.execute(
            "SELECT content_digest FROM versions WHERE project_id = ? AND variant_id = ? AND version_id = ?",
            (ref.project_id, ref.variant_id, ref.base_version_id),
        ).fetchone()
        if row is None or str(row["content_digest"]) != ref.base_content_digest:
            raise RepositoryConflictError("historical draft base version identity or content digest does not match")

    def _validate_base(self, ref: DraftRef) -> None:
        effective = self._effective_ref(ref.project_id, ref.variant_id)
        if effective is None:
            if ref.base_version_id is not None:
                raise RepositoryConflictError("a first draft cannot name a base version")
            return
        if ref.base_version_id != effective.version_id or ref.base_content_digest != effective.content_digest:
            raise RepositoryConflictError("draft base must match the effective version identity and content")

    def _row(self, project_id: str, variant_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT revision, payload_json FROM drafts WHERE project_id = ? AND variant_id = ?",
            (project_id, variant_id),
        ).fetchone()

    def _require_project(self, project_id: str) -> None:
        if project_id != self._project_id:
            raise RepositoryConflictError("terminology object belongs to another Project database")


def _appended_actions(
    current: tuple[ManualAction, ...],
    updated: tuple[ManualAction, ...],
) -> tuple[ManualAction, ...]:
    updated_by_id = {item.action_id: item for item in updated}
    if any(updated_by_id.get(item.action_id) != item for item in current):
        raise RepositoryConflictError("draft ManualAction history is append-only")
    current_ids = {item.action_id for item in current}
    return tuple(item for item in updated if item.action_id not in current_ids)


__all__ = ["DraftStore"]
