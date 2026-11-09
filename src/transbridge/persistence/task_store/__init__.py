"""Independent task database and its scoped views."""

from base64 import urlsafe_b64encode
import json
from pathlib import Path

from transbridge.application.tasks.history import TASKS_MANAGE_PERMISSION, TaskHistoryError, TaskHistoryRecord

from .connection import TaskStoreError, encode, transaction
from .decisions import ApplicationDecisions
from .identity import TaskScope, claim_run
from .recovery import RecoveryView
from .reports import ReportsView

__all__ = ["TaskStore", "TaskStoreError"]


class TaskStore:
    """The desktop manager gets a trusted view; caller scopes are enforced in SQL."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "tasks.sqlite3"

    def recovery(self, *, owner_id=None, session_id=None, entrypoint=None):
        return RecoveryView(self, TaskScope(owner_id, session_id, entrypoint))

    def run_directory(self, run_id):
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("Task run ID must be a non-empty string")
        segment = urlsafe_b64encode(run_id.encode()).decode("ascii").rstrip("=")
        return self.root / "artifacts" / f"run-{segment}"

    def history(self, *, owner_id=None, session_id=None, entrypoint=None):
        return ReportsView(self, TaskScope(owner_id, session_id, entrypoint))

    def application_decisions(self, scope=None):
        return ApplicationDecisions(self.path, scope or TaskScope())

    @property
    def decisions(self):
        return self.application_decisions()

    def append(self, record: TaskHistoryRecord):
        scope = record.owner
        payload = encode(record.to_dict())
        with transaction(self.path, write=True) as db:
            row = claim_run(
                db,
                record.run_id,
                owner_id=scope.owner_id,
                session_id=scope.session_id,
                entrypoint=scope.entrypoint,
                project_id=scope.project_id,
                variant_id=scope.variant_id,
                created_at=record.created_at.isoformat(),
            )
            if row["terminal"] is not None and row["terminal"] != payload:
                raise TaskHistoryError("history_immutable_conflict", "Task already has a different terminal record")
            db.execute("UPDATE run SET terminal = ? WHERE run_id = ? AND terminal IS NULL", (payload, record.run_id))

    def list(self, actor, *, limit=None):
        if limit is not None and (isinstance(limit, bool) or limit < 0):
            raise ValueError("limit must not be negative")
        parameters = ()
        sql = "SELECT terminal FROM run WHERE terminal IS NOT NULL AND deleted = 0"
        if TASKS_MANAGE_PERMISSION not in actor.permissions:
            sql += " AND owner_id = ? AND session_id IS ? AND entrypoint = ? AND project_id IS ? AND variant_id IS ?"
            parameters = (actor.owner_id, actor.session_id, actor.entrypoint, actor.project_id, actor.variant_id)
        with transaction(self.path) as db:
            if db is None:
                return ()
            return tuple(
                TaskHistoryRecord.from_dict(json.loads(row[0]))
                for row in db.execute(
                    sql + " ORDER BY created_at DESC, run_id DESC LIMIT ?",
                    (*parameters, -1 if limit is None else limit),
                )
            )
