"""Reuse unchanged report payloads while publishing small task status updates."""

from datetime import UTC, datetime
from pathlib import Path

from transbridge.application.translation.task_history import TaskHistoryStore


class TaskRecordPersistence:
    """Owned by one serialized record writer; accessed only by its worker."""

    def __init__(self):
        self._record = None
        self._rows = {}
        self._sources = ()
        self._cancelled = False
        self._payload = None

    def save(self, directory, sources, ledger, request, *, created_at, state, **status):
        from .source_execution import build_task_record
        from .task_record_inputs import ledger_outcomes

        directory = None if directory is None else Path(directory)
        cancelled = state == "cancelled"
        reusable = (
            self._record is not None
            and self._payload is not None
            and self._payload.parent.parent == directory
            and self._sources == sources
            and self._cancelled == cancelled
            and len(self._rows) == len(ledger.entries)
            and all(self._rows.get(key) is row for key, row in ledger.entries.items())
        )
        if reusable:
            ended_at = self._record.get("ended_at") if self._record["state"] == state else None
            if state in {"completed", "cancelled", "failed", "error"} and ended_at is None:
                ended_at = datetime.now(UTC).isoformat()
            fields = {"state": state, "ended_at": ended_at, **status}
            record = {**self._record, **fields}
            changed = any(self._record.get(key) != value for key, value in fields.items())
            if changed:
                record["updated_at"] = datetime.now(UTC).isoformat()
        else:
            outcomes = ledger_outcomes(sources, ledger, cancelled=cancelled)
            record = build_task_record(outcomes, request, state=state, applied=status["applied"], saved=status["saved"])
            record.update(created_at=created_at, **status)
        try:
            if directory is None:
                raise RuntimeError("项目目录不可用")
            store = TaskHistoryStore(directory)
            if reusable:
                # This compares only small metadata, and rejects a different
                # payload generation instead of applying a stale writer's state.
                store.update_metadata(request.run_id, expected_record_file=self._payload.name, **fields)
            else:
                self._payload = store.save(record)
        except Exception as exc:
            return record, str(exc)
        self._record = record
        self._rows = ledger.entries
        self._sources = sources
        self._cancelled = cancelled
        return record, ""
