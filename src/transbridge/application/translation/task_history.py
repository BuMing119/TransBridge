"""Project-local result history. Records are reports, never resumable jobs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import uuid

from transbridge.application.contracts import Diagnostic, OperationOutcome
from transbridge.application.io import EntryKey, EntryRevision

from .postprocess import PostProcessCandidate, PostProcessStageOutcome, ReportSnapshot


def atomic_json(path: Path, value: Mapping) -> None:
    """Publish a complete record, leaving the previous file intact on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".record-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class TaskHistoryStore:
    """Metadata-only listing plus immutable payload generations for atomic saves."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _directory(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("task history run_id must not be empty")
        return self.root / hashlib.sha256(run_id.encode("utf-8")).hexdigest()

    def save(self, record: dict) -> Path:
        directory = self._directory(record["run_id"])
        previous_path = directory / "metadata.json"
        previous = self._read(previous_path) if previous_path.exists() else {}
        payload_name = f"record-{uuid.uuid4().hex}.json"
        payload_path = directory / payload_name
        # The metadata switch is the commit point. Readers always see a complete generation.
        atomic_json(payload_path, record)
        metadata = {key: value for key, value in record.items() if key != "sources"}
        metadata["created_at"] = previous.get("created_at", record["created_at"])
        if previous.get("state") == record.get("state") and previous.get("ended_at"):
            metadata["ended_at"] = previous["ended_at"]
        metadata["sources"] = [
            {"key": source["key"], "label": source["label"], "error": source.get("error", "")}
            for source in record.get("sources", ())
        ]
        metadata["record_file"] = payload_name
        try:
            atomic_json(previous_path, metadata)
        except BaseException:
            payload_path.unlink(missing_ok=True)
            raise
        return payload_path

    def list_records(self) -> tuple[dict, ...]:
        records = [self._read(path) for path in self.root.glob("*/metadata.json")]
        return tuple(sorted(records, key=lambda item: item.get("created_at", ""), reverse=True))

    def load(self, run_id: str) -> dict:
        directory = self._directory(run_id)
        metadata = self._read(directory / "metadata.json")
        filename = metadata["record_file"]
        if Path(filename).name != filename or not filename.startswith("record-") or not filename.endswith(".json"):
            raise ValueError("invalid task history payload filename")
        record = self._read(directory / filename)
        record.update({key: value for key, value in metadata.items() if key not in {"sources", "record_file"}})
        return record

    def update_metadata(self, run_id: str, **fields: Any) -> None:
        allowed = {"state", "applied", "saved"}
        if set(fields) - allowed:
            raise ValueError("unsupported task history metadata field")
        path = self._directory(run_id) / "metadata.json"
        metadata = self._read(path)
        metadata.update(fields)
        metadata["updated_at"] = datetime.now(UTC).isoformat()
        atomic_json(path, metadata)

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("expected an object")
            return value
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot read task history {path}: {exc}") from exc


def restore_report_snapshot(data: dict) -> ReportSnapshot:
    """Read the canonical report representation without any model or execution state."""
    run_id = data["run_id"]

    def candidate(item):
        return PostProcessCandidate(
            run_id=run_id,
            entry_key=EntryKey.from_dict(item["entry_key"]),
            before_revision=EntryRevision(item["before_revision"]),
            original=item["original"],
            before_text=item["before"],
            text=item["candidate"],
            stage=item["stage"],
            phases=tuple(item.get("phases", ())),
            accepted=item["accepted"],
            context=item.get("context", ""),
            report_details=tuple(item.get("report_details", {}).items()),
        )

    return ReportSnapshot(
        schema=data["schema"],
        run_id=run_id,
        outcome=OperationOutcome(data["outcome"]),
        input_count=data["counts"]["input"],
        accepted_count=data["counts"]["accepted"],
        candidates=tuple(candidate(item) for item in data["entries"]),
        stage_outcomes=tuple(
            PostProcessStageOutcome(
                phase=stage["phase"],
                candidates=tuple(candidate(item) for item in stage["entries"]),
                diagnostics=tuple(Diagnostic.from_dict(item) for item in stage["diagnostics"]),
                duration_ms=stage["duration_ms"],
            )
            for stage in data.get("stages", ())
        ),
        diagnostics=tuple(Diagnostic.from_dict(item) for item in data["diagnostics"]),
        issue_count=data.get("issues", 0),
        failure_count=data.get("failures", 0),
        timing_ms=tuple(tuple(item) for item in data.get("timing_ms", ())),
        run_spec_summary=data.get("run_spec_summary", {}),
    )
