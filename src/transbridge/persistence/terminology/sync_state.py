"""SQLite terminology synchronization state with transactional CAS commits."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from typing import TYPE_CHECKING

from transbridge.application.terminology.errors import (
    CursorStaleError,
    RepositoryConflictError,
    RevisionConflictError,
    TerminologyNotFoundError,
)
from transbridge.application.terminology.ports import Page, PageRequest, SnapshotCursor
from transbridge.application.terminology_sync.models import (
    TerminologySyncBaseline,
    TerminologySyncCommit,
    TerminologySyncItemLink,
    TerminologySyncItemOutcomeRecord,
    TerminologySyncLine,
    TerminologySyncLineState,
    TerminologySyncOutcome,
    TerminologySyncProfile,
    TerminologySyncTarget,
)

from .connection import StorageMode
from .schema import SCHEMA_VERSION
from .sync_codec import dumps_sync, loads_sync

if TYPE_CHECKING:
    from transbridge.application.terminology_sync.inbound import InboundTerminologyChangeSet

    from .repository import SqliteTerminologyRepository

VARIANT_MAPPING_CONFLICT = "variant_mapping_conflict"
SYNC_STORAGE_UNAVAILABLE = "sync_storage_unavailable"
TARGET_UNVERIFIED = "target_unverified"


class SqliteTerminologySyncState:
    """Narrow adapter composed into the project-isolated terminology repository."""

    def __init__(self, repository: SqliteTerminologyRepository) -> None:
        self._repository = repository
        self._connection = repository._connection

    def resolve_line(
        self,
        project_id: str,
        variant_id: str,
        target: TerminologySyncTarget,
    ) -> TerminologySyncLineState:
        self._repository._require_project(project_id)
        if (
            self._repository.storage_state.schema_version != SCHEMA_VERSION
            or not self._repository.storage_state.integrity_ok
        ):
            return TerminologySyncLineState(None, None, None, False, SYNC_STORAGE_UNAVAILABLE)
        with self._repository._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM terminology_sync_lines "
                "WHERE project_id = ? AND target_id = ? AND retired_at IS NULL",
                (project_id, target.target_id),
            ).fetchone()
            if row is None:
                diagnostic = self._read_diagnostic(target)
                return TerminologySyncLineState(None, None, None, diagnostic is None, diagnostic)
            line = self._decode(str(row["payload_json"]), TerminologySyncLine)
            profile = self._profile(line.line_id)
            baseline = self._baseline(line.line_id)
            if line.variant_id != variant_id:
                return TerminologySyncLineState(line, profile, baseline, False, VARIANT_MAPPING_CONFLICT)
            diagnostic = self._read_diagnostic(target)
            return TerminologySyncLineState(line, profile, baseline, diagnostic is None, diagnostic)

    def activate_line(
        self,
        line: TerminologySyncLine,
        profile: TerminologySyncProfile,
    ) -> TerminologySyncLineState:
        self._validate_line_and_profile(line, profile)
        with self._repository._lock, self._repository.transaction():
            active = self._active_line(line.target.target_id)
            if active is not None and active.line_id != line.line_id:
                return TerminologySyncLineState(
                    active,
                    self._profile(active.line_id),
                    self._baseline(active.line_id),
                    False,
                    VARIANT_MAPPING_CONFLICT,
                )
            existing = self._line(line.line_id)
            if existing is not None:
                existing_profile = self._profile(line.line_id)
                if existing != line or existing_profile != profile:
                    raise RepositoryConflictError("sync line identity already contains different state")
            else:
                self._insert_line(line, profile)
        return self.resolve_line(line.project_id, line.variant_id, line.target)

    def replace_active_variant_mapping(
        self,
        line: TerminologySyncLine,
        profile: TerminologySyncProfile,
        *,
        expected_mapping_revision: int,
        retired_at: str,
    ) -> TerminologySyncLineState:
        self._validate_line_and_profile(line, profile)
        if profile.mapping_revision != expected_mapping_revision + 1:
            raise RevisionConflictError(expected_mapping_revision + 1, profile.mapping_revision)
        with self._repository._lock, self._repository.transaction():
            active = self._active_line(line.target.target_id)
            if active is None:
                raise RevisionConflictError(expected_mapping_revision, None)
            active_profile = self._profile(active.line_id)
            if active_profile is None or active_profile.mapping_revision != expected_mapping_revision:
                raise RevisionConflictError(
                    expected_mapping_revision,
                    None if active_profile is None else active_profile.mapping_revision,
                )
            if active.line_id == line.line_id:
                raise RepositoryConflictError("replacement mapping must use a new canonical sync line")
            if self._line(line.line_id) is not None:
                raise RepositoryConflictError("retired sync lines cannot be reactivated")
            retired = replace(active, retired_at=retired_at)
            self._connection.execute(
                "UPDATE terminology_sync_lines SET retired_at = ?, payload_json = ? "
                "WHERE line_id = ? AND retired_at IS NULL",
                (retired.retired_at, dumps_sync(retired), active.line_id),
            )
            self._insert_line(line, profile)
        return self.resolve_line(line.project_id, line.variant_id, line.target)

    def update_profile(
        self,
        profile: TerminologySyncProfile,
        *,
        expected_revision: int,
    ) -> TerminologySyncProfile:
        self._repository._ensure_writable()
        with self._repository._lock, self._repository.transaction():
            current = self._profile(profile.line_id)
            if current is None:
                raise TerminologyNotFoundError("sync profile was not found")
            if current.revision != expected_revision:
                raise RevisionConflictError(expected_revision, current.revision)
            if profile.revision != expected_revision + 1:
                raise RevisionConflictError(expected_revision + 1, profile.revision)
            if profile.mapping_revision != current.mapping_revision:
                raise RepositoryConflictError("profile updates cannot change the active mapping revision")
            cursor = self._connection.execute(
                "UPDATE terminology_sync_profiles SET revision = ?, payload_json = ? "
                "WHERE line_id = ? AND revision = ?",
                (profile.revision, dumps_sync(profile), profile.line_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise RevisionConflictError(expected_revision, self._profile(profile.line_id).revision)
        return profile

    def get_baseline(self, line_id: str) -> TerminologySyncBaseline | None:
        with self._repository._lock:
            self._require_readable_sync_schema()
            return self._baseline(line_id)

    def list_item_links(
        self,
        line_id: str,
        request: PageRequest = PageRequest(),
    ) -> Page[TerminologySyncItemLink]:
        with self._repository._lock:
            self._require_readable_sync_schema()
            self._require_line(line_id)
            return self._page(
                table="terminology_sync_item_links",
                owner_column="line_id",
                owner_value=line_id,
                stable_column="item_id",
                revision_column="revision",
                request=request,
                expected_type=TerminologySyncItemLink,
            )

    def list_outcomes(
        self,
        run_id: str,
        request: PageRequest = PageRequest(),
    ) -> Page[TerminologySyncItemOutcomeRecord]:
        with self._repository._lock:
            self._require_readable_sync_schema()
            return self._page(
                table="terminology_sync_outcomes",
                owner_column="run_id",
                owner_value=run_id,
                stable_column="outcome_id",
                revision_column=None,
                request=request,
                expected_type=TerminologySyncItemOutcomeRecord,
            )

    def commit_run(
        self,
        commit: TerminologySyncCommit,
        *,
        expected_baseline_revision: int | None,
    ) -> TerminologySyncBaseline:
        self._repository._ensure_writable()
        with self._repository._lock, self._repository.transaction():
            self._commit_run_unlocked(commit, expected_baseline_revision=expected_baseline_revision)
        return commit.baseline

    def commit_run_with_inbound(
        self,
        commit: TerminologySyncCommit,
        change_set: InboundTerminologyChangeSet,
        *,
        expected_baseline_revision: int | None,
    ) -> TerminologySyncBaseline:
        """Commit a bidirectional run and its immutable inbound facts atomically."""

        self._repository._ensure_writable()
        with self._repository._lock, self._repository.transaction():
            self._commit_run_unlocked(commit, expected_baseline_revision=expected_baseline_revision)
            self._repository.inbound_reviews._save_change_set_unlocked(change_set)
        return commit.baseline

    def _commit_run_unlocked(
        self,
        commit: TerminologySyncCommit,
        *,
        expected_baseline_revision: int | None,
    ) -> None:
        line = self._require_line(commit.run.line_id)
        if not line.active:
            raise RepositoryConflictError("cannot commit a run to a retired sync line")
        if commit.run.target_id != line.target.target_id:
            raise RepositoryConflictError("sync run target does not match its line")
        if commit.run.baseline_revision != expected_baseline_revision:
            raise RevisionConflictError(expected_baseline_revision or 0, commit.run.baseline_revision)
        current_baseline = self._baseline(line.line_id)
        actual_revision = None if current_baseline is None else current_baseline.revision
        if actual_revision != expected_baseline_revision:
            raise RevisionConflictError(expected_baseline_revision or 0, actual_revision)
        expected_new_revision = 0 if expected_baseline_revision is None else expected_baseline_revision + 1
        if commit.baseline.revision != expected_new_revision:
            raise RevisionConflictError(expected_new_revision, commit.baseline.revision)
        self._ensure_run_absent(commit.run.run_id)
        self._connection.execute(
            "INSERT INTO terminology_sync_runs("
            "run_id, line_id, plan_id, owner_id, target_id, outcome, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                commit.run.run_id,
                line.line_id,
                commit.run.plan_id,
                commit.run.owner_id,
                commit.run.target_id,
                commit.run.outcome.value,
                dumps_sync(commit.run),
            ),
        )
        for outcome in commit.outcomes:
            self._insert_outcome(outcome)
        for update in commit.item_links:
            self._upsert_item_link(update.link, expected_revision=update.expected_revision)
        if current_baseline is None:
            self._connection.execute(
                "INSERT INTO terminology_sync_baselines(line_id, revision, completed_run_id, payload_json) "
                "VALUES (?, ?, ?, ?)",
                (
                    commit.baseline.line_id,
                    commit.baseline.revision,
                    commit.baseline.completed_run_id,
                    dumps_sync(commit.baseline),
                ),
            )
        else:
            cursor = self._connection.execute(
                "UPDATE terminology_sync_baselines SET revision = ?, completed_run_id = ?, payload_json = ? "
                "WHERE line_id = ? AND revision = ?",
                (
                    commit.baseline.revision,
                    commit.baseline.completed_run_id,
                    dumps_sync(commit.baseline),
                    line.line_id,
                    expected_baseline_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflictError(expected_baseline_revision, self._baseline(line.line_id).revision)

    def _validate_line_and_profile(self, line: TerminologySyncLine, profile: TerminologySyncProfile) -> None:
        self._repository._require_project(line.project_id)
        self._repository._ensure_writable()
        if not line.active:
            raise RepositoryConflictError("a new active sync line cannot already be retired")
        if profile.line_id != line.line_id:
            raise RepositoryConflictError("sync profile belongs to another line")
        if profile.revision != line.profile_revision:
            raise RepositoryConflictError("sync line profile revision does not match its profile")

    def _insert_line(self, line: TerminologySyncLine, profile: TerminologySyncProfile) -> None:
        self._connection.execute(
            "INSERT INTO terminology_sync_lines("
            "line_id, project_id, variant_id, target_id, endpoint, account_user_id, remote_project_id, "
            "profile_revision, created_at, retired_at, payload_json"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                line.line_id,
                line.project_id,
                line.variant_id,
                line.target.target_id,
                line.target.endpoint,
                line.target.account_user_id,
                line.target.remote_project_id,
                line.profile_revision,
                line.created_at,
                line.retired_at,
                dumps_sync(line),
            ),
        )
        self._connection.execute(
            "INSERT INTO terminology_sync_profiles(line_id, revision, mapping_revision, payload_json) "
            "VALUES (?, ?, ?, ?)",
            (profile.line_id, profile.revision, profile.mapping_revision, dumps_sync(profile)),
        )

    def _insert_outcome(self, outcome: TerminologySyncItemOutcomeRecord) -> None:
        if self._connection.execute(
            "SELECT 1 FROM terminology_sync_outcomes WHERE outcome_id = ?", (outcome.outcome_id,)
        ).fetchone():
            raise RepositoryConflictError("sync outcome records are append-only")
        self._connection.execute(
            "INSERT INTO terminology_sync_outcomes(outcome_id, run_id, line_id, item_id, status, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                outcome.outcome_id,
                outcome.run_id,
                outcome.line_id,
                outcome.item_id,
                outcome.status.value,
                dumps_sync(outcome),
            ),
        )

    def _upsert_item_link(self, link: TerminologySyncItemLink, *, expected_revision: int | None) -> None:
        row = self._connection.execute(
            "SELECT revision, payload_json FROM terminology_sync_item_links WHERE line_id = ? AND item_id = ?",
            (link.line_id, link.item_id),
        ).fetchone()
        actual_revision = None if row is None else int(row["revision"])
        if actual_revision != expected_revision:
            raise RevisionConflictError(expected_revision or 0, actual_revision)
        expected_new = 0 if expected_revision is None else expected_revision + 1
        if link.revision != expected_new:
            raise RevisionConflictError(expected_new, link.revision)
        previous = None if row is None else self._decode(str(row["payload_json"]), TerminologySyncItemLink)
        if link.last_outcome not in {TerminologySyncOutcome.CONFIRMED, TerminologySyncOutcome.RECONCILED}:
            if link.common_content_digest is not None and (
                previous is None or link.common_content_digest != previous.common_content_digest
            ):
                raise RepositoryConflictError("unconfirmed outcome cannot advance common content digest")
        if link.remote_id is not None and link.tombstone.value == "live":
            collision = self._connection.execute(
                "SELECT item_id FROM terminology_sync_item_links "
                "WHERE line_id = ? AND remote_id = ? AND tombstone = 'live' AND item_id != ?",
                (link.line_id, link.remote_id, link.item_id),
            ).fetchone()
            if collision is not None:
                raise RepositoryConflictError("remote ID is already bound to another live sync item")
        if row is None:
            self._connection.execute(
                "INSERT INTO terminology_sync_item_links("
                "line_id, item_id, revision, remote_id, tombstone, payload_json"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (link.line_id, link.item_id, link.revision, link.remote_id, link.tombstone.value, dumps_sync(link)),
            )
        else:
            cursor = self._connection.execute(
                "UPDATE terminology_sync_item_links "
                "SET revision = ?, remote_id = ?, tombstone = ?, payload_json = ? "
                "WHERE line_id = ? AND item_id = ? AND revision = ?",
                (
                    link.revision,
                    link.remote_id,
                    link.tombstone.value,
                    dumps_sync(link),
                    link.line_id,
                    link.item_id,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                raise RevisionConflictError(expected_revision, actual_revision)

    def _read_diagnostic(self, target: TerminologySyncTarget) -> str | None:
        if self._repository.storage_state.mode is StorageMode.READ_ONLY:
            return SYNC_STORAGE_UNAVAILABLE
        if not target.verified:
            return TARGET_UNVERIFIED
        return None

    def _active_line(self, target_identity: str) -> TerminologySyncLine | None:
        row = self._connection.execute(
            "SELECT payload_json FROM terminology_sync_lines "
            "WHERE project_id = ? AND target_id = ? AND retired_at IS NULL",
            (self._repository.project_id, target_identity),
        ).fetchone()
        return None if row is None else self._decode(str(row["payload_json"]), TerminologySyncLine)

    def _line(self, line_id: str) -> TerminologySyncLine | None:
        row = self._connection.execute(
            "SELECT payload_json FROM terminology_sync_lines WHERE line_id = ?", (line_id,)
        ).fetchone()
        return None if row is None else self._decode(str(row["payload_json"]), TerminologySyncLine)

    def _require_line(self, line_id: str) -> TerminologySyncLine:
        line = self._line(line_id)
        if line is None:
            raise TerminologyNotFoundError("sync line was not found")
        self._repository._require_project(line.project_id)
        return line

    def _profile(self, line_id: str) -> TerminologySyncProfile | None:
        row = self._connection.execute(
            "SELECT payload_json FROM terminology_sync_profiles WHERE line_id = ?", (line_id,)
        ).fetchone()
        return None if row is None else self._decode(str(row["payload_json"]), TerminologySyncProfile)

    def _baseline(self, line_id: str) -> TerminologySyncBaseline | None:
        row = self._connection.execute(
            "SELECT payload_json FROM terminology_sync_baselines WHERE line_id = ?", (line_id,)
        ).fetchone()
        return None if row is None else self._decode(str(row["payload_json"]), TerminologySyncBaseline)

    def _ensure_run_absent(self, run_id: str) -> None:
        if self._connection.execute("SELECT 1 FROM terminology_sync_runs WHERE run_id = ?", (run_id,)).fetchone():
            raise RepositoryConflictError("sync run records are append-only")

    def _require_readable_sync_schema(self) -> None:
        if (
            self._repository.storage_state.schema_version != SCHEMA_VERSION
            or not self._repository.storage_state.integrity_ok
        ):
            raise RepositoryConflictError(SYNC_STORAGE_UNAVAILABLE)

    def _decode[T](self, payload: str, expected_type: type[T]) -> T:
        try:
            return loads_sync(payload, expected_type)
        except ValueError as exc:
            self._repository._mark_corrupt("invalid terminology sync payload", cause=exc)

    def _page[T](
        self,
        *,
        table: str,
        owner_column: str,
        owner_value: str,
        stable_column: str,
        revision_column: str | None,
        request: PageRequest,
        expected_type: type[T],
    ) -> Page[T]:
        revision_sql = "" if revision_column is None else f", {revision_column}"
        snapshot_rows = self._connection.execute(
            f"SELECT {stable_column}{revision_sql}, payload_json FROM {table} "
            f"WHERE {owner_column} = ? ORDER BY {stable_column}",
            (owner_value,),
        ).fetchall()
        digest = hashlib.sha256()
        for row in snapshot_rows:
            digest.update(str(row[stable_column]).encode("utf-8"))
            if revision_column is not None:
                digest.update(f":{int(row[revision_column])}".encode("ascii"))
            digest.update(hashlib.sha256(str(row["payload_json"]).encode("utf-8")).digest())
        snapshot_digest = digest.hexdigest()
        cursor = request.cursor
        if cursor is not None and (
            cursor.snapshot_digest != snapshot_digest
            or cursor.query_fingerprint != request.query_fingerprint
            or cursor.sort_values != (cursor.stable_id,)
        ):
            raise CursorStaleError("sync cursor does not belong to this snapshot and query")
        after = None if cursor is None else cursor.stable_id
        visible_rows = [row for row in snapshot_rows if after is None or str(row[stable_column]) > after]
        visible = visible_rows[: request.limit]
        next_cursor = None
        if len(visible_rows) > request.limit and visible:
            stable_id = str(visible[-1][stable_column])
            next_cursor = SnapshotCursor(snapshot_digest, request.query_fingerprint, (stable_id,), stable_id)
        return Page(
            tuple(self._decode(str(row["payload_json"]), expected_type) for row in visible),
            snapshot_digest,
            next_cursor,
            len(snapshot_rows),
        )


__all__ = [
    "SYNC_STORAGE_UNAVAILABLE",
    "TARGET_UNVERIFIED",
    "VARIANT_MAPPING_CONFLICT",
    "SqliteTerminologySyncState",
]
