"""Immutable result requests and independently acknowledged project receipts."""

from __future__ import annotations

import json

from transbridge.application.io.identity import EntryKey
from transbridge.application.tasks.storage_contracts import (
    ApplicationState,
    ApplyCandidate,
    ApplyRequest,
    TaskApplication,
)

from .connection import TaskStoreError, encode, transaction


class ApplicationDecisions:
    def __init__(self, path, scope):
        self._path, self._scope = path, scope

    def prepare(self, request):
        return self._prepare(request, reuse=False)

    def find_or_prepare(self, request):
        return self._prepare(request, reuse=True)

    def _prepare(self, request, *, reuse):
        payload = _request_payload(request)
        with transaction(self._path, write=True) as db:
            run = self._scope.require(db, request.run_id)
            if (run["project_id"], run["variant_id"]) != (request.project_id, request.variant_id):
                raise TaskStoreError("Task application targets a different project or version")
            existing = db.execute(
                "SELECT * FROM application WHERE operation_id = ?", (request.operation_id,)
            ).fetchone()
            if existing is not None:
                if existing["run_id"] != request.run_id or existing["request"] != payload:
                    raise TaskStoreError("Operation ID already identifies a different task application")
                return _application(existing)
            if reuse:
                existing = db.execute(
                    "SELECT * FROM application WHERE run_id = ? AND request = ? ORDER BY rowid DESC LIMIT 1",
                    (request.run_id, payload),
                ).fetchone()
                if existing is not None:
                    return _application(existing)
            for candidate in request.candidates:
                if not db.execute(
                    "SELECT 1 FROM candidate_head WHERE run_id = ? AND entry_key = ? AND revision = ?",
                    (request.run_id, encode(candidate.entry_key.to_dict()), candidate.revision),
                ).fetchone():
                    raise TaskStoreError("Task application candidate revision changed or is unavailable")
            db.execute(
                "INSERT INTO application VALUES (?, ?, ?, ?, NULL)",
                (request.operation_id, request.run_id, payload, ApplicationState.PREPARED),
            )
        return TaskApplication(request, ApplicationState.PREPARED)

    def get(self, operation_id):
        with transaction(self._path) as db:
            return _application(self._require(db, operation_id))

    def list(self, run_id):
        with transaction(self._path) as db:
            self._scope.require(db, run_id)
            return tuple(
                _application(row)
                for row in db.execute("SELECT * FROM application WHERE run_id = ? ORDER BY rowid", (run_id,))
            )

    def set_state(self, operation_id, state):
        state = ApplicationState(state)
        if state is ApplicationState.COMMITTED:
            raise ValueError("A project receipt is required to acknowledge a committed result")
        with transaction(self._path, write=True) as db:
            existing = self._require(db, operation_id)
            if existing["receipt"] is None:
                db.execute("UPDATE application SET state = ? WHERE operation_id = ?", (state, operation_id))
            return _application(self._require(db, operation_id))

    def candidate_values(self, request):
        with transaction(self._path) as db:
            self._scope.require(db, request.run_id)
            result = {}
            for candidate in request.candidates:
                row = db.execute(
                    "SELECT content FROM candidate WHERE run_id = ? AND entry_key = ? AND revision = ?",
                    (request.run_id, encode(candidate.entry_key.to_dict()), candidate.revision),
                ).fetchone()
                if row is None:
                    raise TaskStoreError("Task application references a missing candidate revision")
                value = json.loads(row[0])
                result[candidate.entry_key] = (value["text"], value["stage"])
            return result

    def acknowledge(self, operation_id, receipt, state=ApplicationState.COMMITTED):
        payload = encode(receipt)
        with transaction(self._path, write=True) as db:
            existing = self._require(db, operation_id)
            if existing["receipt"] is not None:
                if existing["receipt"] != payload:
                    raise TaskStoreError("Project receipt differs from the acknowledged result")
                return _application(existing)
            db.execute(
                "UPDATE application SET state = ?, receipt = ? WHERE operation_id = ?",
                (state, payload, operation_id),
            )
            _acknowledge_heads(db, _application(existing).request, receipt)
            return _application(self._require(db, operation_id))

    def _require(self, db, operation_id):
        row = (
            None
            if db is None
            else db.execute("SELECT * FROM application WHERE operation_id = ?", (operation_id,)).fetchone()
        )
        if row is None:
            raise TaskStoreError("Task application does not exist")
        self._scope.require(db, row["run_id"])
        return row


def _acknowledge_heads(db, request, receipt):
    confirmed = {EntryKey.from_dict(row["entry_key"]) for row in receipt["entries"]}
    restored = request.decision == "restore"
    for candidate in request.candidates:
        if candidate.entry_key not in confirmed:
            continue
        identity = (request.run_id, encode(candidate.entry_key.to_dict()), candidate.revision)
        row = db.execute(
            "SELECT decision FROM candidate_head WHERE run_id = ? AND entry_key = ? AND revision = ?", identity
        ).fetchone()
        if row is None:
            continue
        decision = json.loads(row[0])
        decision.update(
            applied=not restored,
            decision="rejected" if restored else "accepted",
            merge_status="restored" if restored else "applied",
            merge_reason="",
            decision_revision=decision.get("decision_revision", 0) + 1,
        )
        db.execute(
            "UPDATE candidate_head SET decision = ? WHERE run_id = ? AND entry_key = ? AND revision = ?",
            (encode(decision), *identity),
        )


def _request_payload(request):
    return encode({
        "project_id": request.project_id,
        "variant_id": request.variant_id,
        "policy": request.policy,
        "decision": request.decision,
        "candidates": [
            [item.entry_key.to_dict(), item.revision] for item in sorted(request.candidates, key=lambda i: i.entry_key)
        ],
    })


def _application(row):
    request = json.loads(row["request"])
    candidates = tuple(ApplyCandidate(EntryKey.from_dict(key), revision) for key, revision in request.pop("candidates"))
    return TaskApplication(
        ApplyRequest(row["operation_id"], row["run_id"], candidates=candidates, **request),
        ApplicationState(row["state"]),
        None if row["receipt"] is None else json.loads(row["receipt"]),
    )
