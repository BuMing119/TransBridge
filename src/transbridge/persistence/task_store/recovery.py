"""SQLite-backed task execution inputs, state, and candidate access."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import shutil

from transbridge.application.translation.task_recovery import _validate, _validate_fields
from transbridge.application.translation.task_recovery_lease import TaskRecoveryLease
from transbridge.application.translation.task_recovery_storage import _FIXED, recovery_summary
from transbridge.application.translation.task_result_recovery import recovery_result_rows

from .connection import TaskStoreError, encode, transaction
from .identity import claim_run
from .payloads import candidate_payload, task_payload
from .results import load_results, pack_applied, save_results, unpack_applied

_FIELDS = _FIXED - {"prepared_source", "candidate_rows"}
_IDENTITY = frozenset({"task_id", "project_id", "variant_id", "owner_id", "session_id", "entrypoint", "created_at"})
_INITIAL_INPUT = frozenset({"sources", "execution_config", "reference_snapshot", "config_digest", "mode", "overwrite"})


class RecoveryView:
    def __init__(self, store, scope):
        self.root = store.root / "artifacts"
        self.run_directory = store.run_directory
        self._db = store.path
        self._scope = scope
        self._lease = None
        self.decisions = store.application_decisions(scope)

    def acquire(self, task_id):
        if self._lease is None:
            self._lease = TaskRecoveryLease(self.root / "leases", task_id)
        elif self._lease.task_id != task_id:
            raise RuntimeError("This task view already owns another recovery lease")

    def release(self):
        if self._lease is not None:
            self._lease.close()
            self._lease = None

    def create(self, record):
        record = task_payload(record)
        for name in ("owner_id", "session_id", "entrypoint"):
            value = getattr(self._scope, name)
            if value is not None or (name == "session_id" and self._scope.owner_id is not None):
                if name in record and record[name] != value:
                    raise TaskStoreError(f"Task {name} differs from the caller's scope")
                record[name] = value
        record = _validate(record)
        run_id = record["task_id"]
        with transaction(self._db, write=True) as db:
            row = claim_run(
                db,
                run_id,
                owner_id=record.get("owner_id"),
                session_id=record.get("session_id"),
                entrypoint=record.get("entrypoint"),
                project_id=record["project_id"],
                variant_id=record["variant_id"],
                created_at=record["created_at"],
            )
            if row["recovery"] is not None:
                raise FileExistsError(f"Task recovery record already exists: {run_id}")
            _write_record(db, run_id, record)
            if record.get("candidate_rows"):
                save_results(db, run_id, record["candidate_rows"], entry_results=True)
        return record

    def load(self, task_id):
        with transaction(self._db) as db:
            row = self._scope.require(db, task_id)
            record = _load_record(db, row)
            if record.get("results_only"):
                record["candidate_rows"] = load_results(db, task_id)
            return record

    def list_summaries(self, *, project_id=None, variant_id=None, states=None, limit=None, offset=0):
        clause, values = self._scope.predicate()
        where = ["deleted = 0", "recovery IS NOT NULL"]
        for name, value in (("project_id", project_id), ("variant_id", variant_id)):
            if value is not None:
                where.append(f"{name} = ?")
                values += (str(value),)
        if states is not None:
            states = tuple(states)
            if not states:
                return ()
            where.append(f"state IN ({','.join('?' for _ in states)})")
            values += states
        if offset < 0 or (limit is not None and limit < 0):
            raise ValueError("Invalid task history page")
        sql = "SELECT recovery FROM run WHERE " + " AND ".join(where) + clause
        # Ownership parameters belong at the end of the predicate.
        scope_size = len(self._scope.predicate()[1])
        if scope_size:
            values = values[scope_size:] + values[:scope_size]
        with transaction(self._db) as db:
            if db is None:
                return ()
            return tuple(
                {**json.loads(row[0]), "recovery_summary": True}
                for row in db.execute(
                    sql + " ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?",
                    (*values, -1 if limit is None else limit, offset),
                )
            )

    def list_records(self):
        return tuple(self.load(item["task_id"]) for item in self.list_summaries())

    def contains(self, task_id):
        clause, values = self._scope.predicate()
        with transaction(self._db) as db:
            return (
                db is not None
                and db.execute(
                    "SELECT 1 FROM run WHERE run_id = ? AND deleted = 0 AND recovery IS NOT NULL" + clause,
                    (task_id, *values),
                ).fetchone()
                is not None
            )

    def update(self, task_id, **fields):
        self.update_metadata(task_id, **fields)
        return self.load(task_id)

    def update_metadata(self, task_id, **fields):
        return self.mutate_metadata(task_id, lambda _: fields, load_fields=tuple(fields))

    def mutate_metadata(self, task_id, change, *, load_fields=()):
        with transaction(self._db, write=True) as db:
            row = self._scope.require(db, task_id)
            previous = _load_record(db, row, fields=load_fields)
            fields = task_payload(change(previous), partial=True)
            for name in fields.keys() & _IDENTITY:
                if previous.get(name) != fields[name]:
                    raise ValueError(f"Task recovery {name} is immutable")
            immutable = fields.keys() & _INITIAL_INPUT
            if immutable:
                original = _load_record(db, row, fields=immutable)
                if any(original.get(name) != fields[name] for name in immutable):
                    raise ValueError("The task's fixed input cannot be changed")
            if fields.keys() & {"sources", "execution_inputs"}:
                _validate({**_load_record(db, row), **fields})
            else:
                _validate_fields({
                    key: value for key, value in fields.items() if key not in _FIELDS | {"candidate_rows"}
                })
            if "candidate_rows" in fields:
                save_results(db, task_id, fields["candidate_rows"], entry_results=True)
            merged = {**previous, **fields, "updated_at": datetime.now(UTC).isoformat()}
            _write_record(db, task_id, merged, changed=fields)
            summary = {
                name: value
                for name, value in json.loads(row["recovery"]).items()
                if name not in {"sources", "scope_count", "execution_source_refs"}
            }
            return {**summary, **fields, "updated_at": merged["updated_at"]}

    def save_ledger_results(self, task_id, ledger):
        self.save_results(task_id, recovery_result_rows(ledger))

    def save_results(self, task_id, rows):
        rows = [candidate_payload(row) for row in rows]
        if rows:
            with transaction(self._db, write=True) as db:
                record = _load_record(db, self._scope.require(db, task_id), fields=())
                save_results(db, task_id, rows, entry_results=bool(record.get("result_kind")))

    def load_results(self, task_id):
        with transaction(self._db) as db:
            record = _load_record(db, self._scope.require(db, task_id), fields=())
            rows = load_results(db, task_id)
            return {"schema": 1, "task_id": task_id, "rows": rows} if rows or record.get("results_only") else None

    def reclaim_checkpoints(self, task_id):
        """Release replay caches only when the completed run needs no continuation."""
        with transaction(self._db) as db:
            record = _load_record(db, self._scope.require(db, task_id), fields=())
        lease = TaskRecoveryLease(self.root / "leases", record.get("origin_task_id") or task_id)
        try:
            with transaction(self._db) as db:
                record = _load_record(db, self._scope.require(db, task_id), fields=("entry_statuses",))
                incomplete = any(
                    row.get("status") not in {"succeeded", "skipped"} for row in record.get("entry_statuses", ())
                )
                outstanding = any(
                    not row.get("applied") and row.get("decision") != "rejected" for row in load_results(db, task_id)
                )
                pending = db.execute(
                    "SELECT 1 FROM application WHERE run_id = ? AND state IN ('prepared', 'staged-unsaved')", (task_id,)
                ).fetchone()
                if record["state"] != "completed" or incomplete or outstanding or pending:
                    raise TaskStoreError("任务仍有待处理成果或可续跑工作，检查点已保留。")
            directory = self.run_directory(task_id)
            if self.root.is_symlink() or self.root.is_junction() or directory.is_symlink() or directory.is_junction():
                raise TaskStoreError("Task artifact directory is redirected outside task storage")
            if directory.resolve().parent != self.root.resolve():
                raise TaskStoreError("Task artifact directory escaped its storage root")
            for name in ("runtime-v2", "ai-proofread-resume", "ai-translation-resume"):
                checkpoint = directory / name
                if checkpoint.exists():
                    if (
                        checkpoint.is_symlink()
                        or checkpoint.is_junction()
                        or checkpoint.resolve().parent != directory.resolve()
                    ):
                        raise TaskStoreError("Task checkpoint escaped its run directory")
                    shutil.rmtree(checkpoint)
        finally:
            lease.close()

    def delete(self, task_id, *, discard_unmerged=False):
        with transaction(self._db) as db:
            pending = self._cleanup_pending(db, task_id)
            if pending:
                record = {}
            else:
                row = self._scope.require(db, task_id)
                record = _load_record(db, row, fields=()) if row["recovery"] is not None else {"state": "completed"}
        lease = TaskRecoveryLease(self.root / "leases", record.get("origin_task_id") or task_id)
        try:
            if not pending:
                self._delete_record(task_id, discard_unmerged=discard_unmerged)
            try:
                _remove_run_artifacts(self.root, self.run_directory(task_id))
                with transaction(self._db, write=True) as db:
                    db.execute(
                        "UPDATE run SET cleanup_pending = 0, owner_id = NULL, session_id = NULL, entrypoint = NULL "
                        "WHERE run_id = ? AND deleted = 1",
                        (task_id,),
                    )
            except (OSError, ValueError) as exc:
                raise TaskStoreError(f"任务记录已删除，但临时材料清理失败，可重试清理。{exc}") from exc
        finally:
            lease.close()

    def list_cleanup_pending(self):
        clause, values = self._scope.predicate()
        with transaction(self._db) as db:
            if db is None:
                return ()
            return tuple(
                row["run_id"]
                for row in db.execute(
                    "SELECT run_id FROM run WHERE deleted = 1 AND cleanup_pending = 1" + clause,
                    values,
                )
            )

    def _cleanup_pending(self, db, task_id):
        clause, values = self._scope.predicate()
        return (
            db is not None
            and db.execute(
                "SELECT 1 FROM run WHERE run_id = ? AND deleted = 1 AND cleanup_pending = 1" + clause,
                (task_id, *values),
            ).fetchone()
            is not None
        )

    def _delete_record(self, task_id, *, discard_unmerged):
        with transaction(self._db, write=True) as db:
            row = self._scope.require(db, task_id)
            record = _load_record(db, row, fields=()) if row["recovery"] is not None else {"state": "completed"}
            unmerged = any(
                not row.get("applied") and row.get("decision") != "rejected" for row in load_results(db, task_id)
            )
            pending = db.execute(
                "SELECT 1 FROM application WHERE run_id = ? AND state = 'staged-unsaved'", (task_id,)
            ).fetchone()
            if pending:
                raise TaskStoreError("Resolve the project's pending application before deleting this task")
            if not discard_unmerged and (
                unmerged or record["state"] not in {"completed", "cancelled", "failed", "error"}
            ):
                raise TaskStoreError("Explicit confirmation is required to discard unfinished or unmerged task data")
            if db.execute("SELECT 1 FROM report_dependency WHERE candidate_run_id = ?", (task_id,)).fetchone():
                raise TaskStoreError("Another report still references this task's results")
            db.execute("DELETE FROM report_dependency WHERE report_run_id = ?", (task_id,))
            for table in ("run_field", "application", "candidate"):
                db.execute(f"DELETE FROM {table} WHERE run_id = ?", (task_id,))
            db.execute(
                "UPDATE run SET deleted = 1, cleanup_pending = 1, recovery = NULL, report = NULL, "
                "report_summary = NULL, terminal = NULL, project_id = NULL, variant_id = NULL WHERE run_id = ?",
                (task_id,),
            )


def _remove_run_artifacts(root, directory):
    if root.is_symlink() or root.is_junction() or directory.is_symlink() or directory.is_junction():
        raise TaskStoreError("Task artifact directory is redirected outside task storage")
    if not directory.exists():
        return
    if directory.resolve().parent != root.resolve():
        raise TaskStoreError("Task artifact directory escaped its storage root")
    shutil.rmtree(directory)


def _write_record(db, run_id, record, *, changed=None):
    payload = {key: value for key, value in record.items() if key not in _FIXED}
    if record.get("result_kind") in {"download", "dictionary", "import"}:
        record = {key: value for key, value in record.items() if key != "entries"}
        changed = None if changed is None else {key: value for key, value in changed.items() if key != "entries"}
    sources = record.get("sources", ())
    payload["sources"] = [{"key": source["key"], "label": source["label"]} for source in sources]
    payload["scope_count"] = recovery_summary(record)["scope_count"]
    if changed is None or "execution_inputs" in changed:
        # The initial and current execution frequently refer to the same input scope.
        # Keep the authoritative source input once and store only distinct scopes.
        execution_inputs = record.get("execution_inputs", {})
        distinct = {run: inputs for run, inputs in execution_inputs.items() if inputs != sources}
        payload["execution_source_refs"] = [run for run, inputs in execution_inputs.items() if inputs == sources]
        record = {**record, "execution_inputs": distinct}
        if changed is not None:
            changed = {**changed, "execution_inputs": distinct}
    db.execute("UPDATE run SET recovery = ?, state = ? WHERE run_id = ?", (encode(payload), record["state"], run_id))
    selected = record.keys() if changed is None else changed.keys()
    for name in selected & _FIELDS:
        value = pack_applied(db, run_id, record[name]) if name == "applied_entries" else record[name]
        db.execute(
            """INSERT INTO run_field VALUES (?, ?, ?) ON CONFLICT (run_id, name)
            DO UPDATE SET value = excluded.value WHERE value != excluded.value""",
            (run_id, name, encode(value)),
        )


def _load_record(db, row, *, fields=None):
    if row["recovery"] is None:
        raise TaskStoreError("Task has no recovery record")
    record = json.loads(row["recovery"])
    if fields is None or fields:
        wanted = _FIELDS if fields is None else _FIELDS.intersection(fields)
        if "execution_inputs" in wanted:
            wanted |= {"sources"}
        if wanted:
            for field in db.execute(
                f"SELECT name, value FROM run_field WHERE run_id = ? AND name IN ({','.join('?' for _ in wanted)})",
                (row["run_id"], *wanted),
            ):
                name, value = field["name"], json.loads(field["value"])
                record[name] = unpack_applied(db, row["run_id"], value) if name == "applied_entries" else value
    if "execution_inputs" in record:
        record["execution_inputs"].update({run: record["sources"] for run in record.get("execution_source_refs", ())})
    if fields is None:
        record.pop("execution_source_refs", None)
        record.pop("scope_count", None)
    return record
