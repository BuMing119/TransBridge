# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Share task identity across execution summaries, recovery, and reports."""

from dataclasses import dataclass

from .connection import TaskStoreError


@dataclass(frozen=True, slots=True)
class TaskScope:
    owner_id: str | None = None
    session_id: str | None = None
    entrypoint: str | None = None

    def predicate(self):
        if self.owner_id is None:
            return "", ()
        clause = " AND owner_id = ? AND session_id IS ?"
        values = (self.owner_id, self.session_id)
        if self.entrypoint is not None:
            clause += " AND entrypoint = ?"
            values += (self.entrypoint,)
        return clause, values

    def require(self, db, run_id):
        clause, values = self.predicate()
        row = (
            None
            if db is None
            else db.execute("SELECT * FROM run WHERE run_id = ? AND deleted = 0" + clause, (run_id, *values)).fetchone()
        )
        if row is None:
            raise TaskStoreError("Task does not exist or is outside the caller's scope")
        return row


def claim_run(db, run_id, *, owner_id, session_id, entrypoint, project_id, variant_id, created_at):
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Task run ID must be a non-empty string")
    db.execute("INSERT OR IGNORE INTO run (run_id) VALUES (?)", (run_id,))
    row = TaskScope().require(db, run_id)
    fields = ("owner_id", "session_id", "entrypoint", "project_id", "variant_id")
    supplied = (owner_id, session_id, entrypoint, project_id, variant_id)
    for field, value in zip(fields, supplied, strict=True):
        if row[field] is not None and value is not None and row[field] != value:
            raise TaskStoreError(f"Task {field} differs from the existing record")
    if owner_id is not None and row["owner_id"] is not None and row["session_id"] != session_id:
        raise TaskStoreError("Task session differs from the existing record")
    db.execute(
        """UPDATE run SET owner_id = COALESCE(owner_id, ?), session_id = COALESCE(session_id, ?),
        entrypoint = COALESCE(entrypoint, ?), project_id = COALESCE(project_id, ?),
        variant_id = COALESCE(variant_id, ?), created_at = COALESCE(created_at, ?) WHERE run_id = ?""",
        (*supplied, created_at, run_id),
    )
    return row
