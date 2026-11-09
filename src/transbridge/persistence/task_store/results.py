"""Immutable candidate generations with independently mutable decision heads."""

from __future__ import annotations

import json

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.task_recovery_results import _validate

from .connection import encode

_DECISION_FIELDS = frozenset({"applied", "decision", "decision_revision", "merge_status", "merge_reason"})


def pack_applied(db, run_id, rows):
    packed = []
    for row in rows:
        key = encode(EntryKey.from_dict(row["entry_key"]).to_dict())
        candidate = db.execute(
            "SELECT c.content, c.revision FROM candidate_head h JOIN candidate c USING (run_id, entry_key, revision) "
            "WHERE h.run_id = ? AND h.entry_key = ?",
            (run_id, key),
        ).fetchone()
        if candidate is not None:
            value = _applied_values(json.loads(candidate["content"]))
            if all(row[name] == expected for name, expected in value.items()):
                row = {name: item for name, item in row.items() if name not in value}
                row["candidate_revision"] = candidate["revision"]
        packed.append(row)
    return packed


def unpack_applied(db, run_id, rows):
    for row in rows:
        revision = row.pop("candidate_revision", None)
        if revision is not None:
            key = encode(EntryKey.from_dict(row["entry_key"]).to_dict())
            candidate = db.execute(
                "SELECT content FROM candidate WHERE run_id = ? AND entry_key = ? AND revision = ?",
                (run_id, key, revision),
            ).fetchone()
            if candidate is None:
                raise ValueError("Saved task application references a missing candidate")
            row.update(_applied_values(json.loads(candidate[0])))
    return rows


def _applied_values(candidate):
    return {"original": candidate["before"]["original"], "text": candidate["text"], "stage": candidate["stage"]}


def load_results(db, run_id):
    rows = db.execute(
        """SELECT c.content, h.decision, c.revision FROM candidate_head h
        JOIN candidate c USING (run_id, entry_key, revision) WHERE h.run_id = ? ORDER BY h.rowid""",
        (run_id,),
    )
    return [
        {**json.loads(row["content"]), **json.loads(row["decision"]), "candidate_revision": row["revision"]}
        for row in rows
    ]


def save_results(db, run_id, rows, *, entry_results=False):
    if not entry_results:
        _validate({"schema": 1, "task_id": run_id, "rows": list(rows)}, run_id)
    for row in rows:
        identity = encode(EntryKey.from_dict(row["entry_key"]).to_dict())
        content = encode({
            key: value for key, value in row.items() if key not in _DECISION_FIELDS | {"candidate_revision"}
        })
        decision = encode({key: value for key, value in row.items() if key in _DECISION_FIELDS})
        old = db.execute(
            """SELECT c.content, h.decision, c.revision FROM candidate_head h
            JOIN candidate c USING (run_id, entry_key, revision) WHERE h.run_id = ? AND h.entry_key = ?""",
            (run_id, identity),
        ).fetchone()
        if old is not None and _is_late(row, old):
            continue
        if old is None or old["content"] != content:
            revision = 1 if old is None else old["revision"] + 1
            db.execute("INSERT INTO candidate VALUES (?, ?, ?, ?)", (run_id, identity, revision, content))
        else:
            revision = old["revision"]
            if old["decision"] == decision:
                continue
        db.execute(
            """INSERT INTO candidate_head VALUES (?, ?, ?, ?) ON CONFLICT (run_id, entry_key)
            DO UPDATE SET revision = excluded.revision, decision = excluded.decision""",
            (run_id, identity, revision, decision),
        )


def _is_late(row, old):
    previous, decision = json.loads(old["content"]), json.loads(old["decision"])
    if row.get("candidate_revision", old["revision"]) != old["revision"]:
        return True
    if previous.get("attempt", 0) != row.get("attempt", 0):
        return previous.get("attempt", 0) > row.get("attempt", 0)
    if decision.get("decision_revision", 0) > row.get("decision_revision", 0):
        return True
    return (
        previous.get("text") == row.get("text")
        and decision.get("decision", "pending") != "pending"
        and row.get("decision", "pending") == "pending"
    )
