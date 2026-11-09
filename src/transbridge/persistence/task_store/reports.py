"""Task reports refer to durable result generations for shared values."""

from __future__ import annotations

from datetime import UTC, datetime
import json

from transbridge.application.io.identity import EntryKey

from .connection import TaskStoreError, encode, transaction
from .identity import claim_run
from .payloads import candidate_payload, report_payload
from .results import load_results, save_results

_REPORT_FIELDS = frozenset({
    "state",
    "applied",
    "saved",
    "project_saved",
    "snapshot_saved",
    "version_label",
    "ended_at",
})
_SHARED = {
    "original": ("before", "original"),
    "before": ("before", "translation"),
    "context": ("before", "context"),
    "candidate": ("text",),
    "stage": ("stage",),
}


class ReportsView:
    def __init__(self, store, scope):
        self._db, self._scope = store.path, scope

    def save(self, record, *, candidates=(), result_run_id=None, owner=None):
        run_id = record["run_id"]
        result_run_id = result_run_id or run_id
        with transaction(self._db, write=True) as db:
            owner = owner if self._scope.owner_id is None and owner is not None else self._scope
            row = claim_run(
                db,
                run_id,
                owner_id=owner.owner_id,
                session_id=owner.session_id,
                entrypoint=owner.entrypoint,
                project_id=record.get("project_id"),
                variant_id=record.get("variant_id"),
                created_at=record["created_at"],
            )
            result_row = db.execute(
                "SELECT recovery FROM run WHERE run_id = ? AND deleted = 0", (result_run_id,)
            ).fetchone()
            if result_row is not None and result_row[0] is not None:
                self._scope.require(db, result_run_id)
                if candidates:
                    save_results(db, result_run_id, [candidate_payload(item) for item in candidates])
            by_key = {encode(item["entry_key"]): item for item in load_results(db, result_run_id)}
            report = _pack(report_payload(record), by_key, result_run_id)
            if row["report"] is not None:
                old = json.loads(row["report"])
                report["created_at"] = old["created_at"]
                if old.get("state") == report.get("state") and old.get("ended_at"):
                    report["ended_at"] = old["ended_at"]
            revision = row["report_revision"] + 1
            db.execute("DELETE FROM report_dependency WHERE report_run_id = ?", (run_id,))
            if result_run_id != run_id and any("result_reference" in item for item in _entries(report)):
                db.execute("INSERT INTO report_dependency VALUES (?, ?)", (run_id, result_run_id))
            db.execute(
                "UPDATE run SET report = ?, report_summary = ?, report_revision = ? WHERE run_id = ?",
                (encode(report), encode(_summary(report)), revision, run_id),
            )
        return revision

    def load(self, run_id):
        with transaction(self._db) as db:
            row = self._scope.require(db, run_id)
            if row["report"] is None:
                raise TaskStoreError("Task has no result report")
            return _unpack(db, json.loads(row["report"]))

    def contains(self, run_id):
        clause, values = self._scope.predicate()
        with transaction(self._db) as db:
            return (
                db is not None
                and db.execute(
                    "SELECT 1 FROM run WHERE run_id = ? AND deleted = 0 AND report IS NOT NULL" + clause,
                    (run_id, *values),
                ).fetchone()
                is not None
            )

    def list_records(self, *, limit=None, offset=0):
        clause, values = self._scope.predicate()
        with transaction(self._db) as db:
            if db is None:
                return ()
            return tuple(
                json.loads(row[0])
                for row in db.execute(
                    "SELECT report_summary FROM run WHERE deleted = 0 AND report_summary IS NOT NULL"
                    + clause
                    + " ORDER BY created_at DESC, run_id DESC LIMIT ? OFFSET ?",
                    (*values, -1 if limit is None else limit, offset),
                )
            )

    def update_metadata(self, run_id, *, expected_record_file=None, **fields):
        if fields.keys() - _REPORT_FIELDS:
            raise ValueError("Unsupported task history metadata field")
        with transaction(self._db, write=True) as db:
            row = self._scope.require(db, run_id)
            if row["report"] is None:
                raise TaskStoreError("Task has no result report")
            if expected_record_file is not None and row["report_revision"] != expected_record_file:
                raise TaskStoreError("Task report generation changed before its status update")
            report = json.loads(row["report"])
            if not all(report.get(name) == value for name, value in fields.items()):
                report.update(fields, updated_at=datetime.now(UTC).isoformat())
                db.execute(
                    "UPDATE run SET report = ?, report_summary = ?, report_revision = report_revision + 1 "
                    "WHERE run_id = ?",
                    (encode(report), encode(_summary(report)), run_id),
                )
                return row["report_revision"] + 1
            return row["report_revision"]


def _summary(report):
    return {
        **report,
        "sources": [
            {"key": source["key"], "label": source["label"], "error": source.get("error", "")}
            for source in report.get("sources", ())
        ],
    }


def _entries(report):
    for source in report.get("sources", ()):
        snapshot = source.get("snapshot", {})
        yield from snapshot.get("entries", ())
        for stage in snapshot.get("stages", ()):
            yield from stage.get("entries", ())


def _shared(candidate, field):
    value = candidate
    for step in _SHARED[field]:
        value = value[step]
    return value


def _pack(record, candidates, result_run_id):
    report = json.loads(encode(record))
    for item in _entries(report):
        candidate = candidates.get(encode(EntryKey.from_dict(item["entry_key"]).to_dict()))
        if candidate is None:
            continue
        fields = [field for field in _SHARED if field in item and item[field] == _shared(candidate, field)]
        if fields:
            item["result_reference"] = [result_run_id, candidate["candidate_revision"], fields]
            for field in fields:
                del item[field]
    return report


def _unpack(db, report):
    for item in _entries(report):
        reference = item.pop("result_reference", None)
        if reference is None:
            continue
        run_id, revision, fields = reference
        row = db.execute(
            "SELECT content FROM candidate WHERE run_id = ? AND entry_key = ? AND revision = ?",
            (run_id, encode(EntryKey.from_dict(item["entry_key"]).to_dict()), revision),
        ).fetchone()
        if row is None:
            raise TaskStoreError("Task report candidate is missing")
        candidate = json.loads(row[0])
        item.update({field: _shared(candidate, field) for field in fields})
    return report
