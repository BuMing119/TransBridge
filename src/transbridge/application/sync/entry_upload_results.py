"""Retain entry-upload inputs and remote receipts independently of local association."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4

from transbridge.application.translation.task_history import atomic_json

_PENDING = {}
_PENDING_LOCK = RLock()


class EntryUploadResultStore:
    """Write each remote receipt atomically before attempting a local association."""

    def __init__(self, root):
        self.root = Path(root) / "entry-upload-results"
        self.diagnostics = []

    def directory(self, result_id):
        if (
            not isinstance(result_id, str)
            or len(result_id) != 32
            or any(c not in "0123456789abcdef" for c in result_id)
        ):
            raise ValueError("上传成果 ID 无效。")
        return self.root / result_id

    def create(self, target, inputs):
        record = {
            "schema": 1,
            "result_id": uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "target": deepcopy(target),
            "inputs": deepcopy(inputs),
            "receipts": [],
            "state": "running",
        }
        atomic_json(self.directory(record["result_id"]) / "request.json", record)
        atomic_json(
            self.directory(record["result_id"]) / "target.json",
            {key: record[key] for key in ("schema", "result_id", "created_at", "target")},
        )
        return record

    def save_receipt(self, record, receipt):
        index = receipt["index"]
        if type(index) is not int or not 0 <= index < len(record["inputs"]):
            raise ValueError("上传回执索引无效。")
        atomic_json(self.directory(record["result_id"]) / f"receipt-{index:08d}.json", receipt)

    def finish(self, record):
        atomic_json(
            self.directory(record["result_id"]) / "summary.json",
            {key: deepcopy(value) for key, value in record.items() if key not in {"inputs", "receipts", "target"}},
        )

    def load(self, result_id, *, expected_owner=None):
        directory = self.directory(result_id)
        with _PENDING_LOCK:
            pending = deepcopy(_PENDING.get(str(directory.resolve())))
        if pending is not None:
            if expected_owner is not None and any(
                pending["target"].get(key) != value for key, value in expected_owner.items()
            ):
                raise ValueError("上传成果不属于当前会话、所有者或工程版本。")
            return pending
        header = json.loads((directory / "target.json").read_text(encoding="utf-8"))
        if expected_owner is not None and any(
            header["target"].get(key) != value for key, value in expected_owner.items()
        ):
            raise ValueError("上传成果不属于当前会话、所有者或工程版本。")
        record = json.loads((directory / "request.json").read_text(encoding="utf-8"))
        if record.get("schema") != 1 or record.get("result_id") != result_id:
            raise ValueError("上传成果记录无效。")
        if record["target"] != header["target"]:
            raise ValueError("上传成果与其所有者索引不一致。")
        summary = directory / "summary.json"
        if summary.exists():
            record.update(json.loads(summary.read_text(encoding="utf-8")))
        record["receipts"] = [
            json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("receipt-*.json"))
        ]
        return record

    def retain_pending(self, record):
        """Keep an unsaved remote receipt reachable for a local-only persistence retry."""
        with _PENDING_LOCK:
            _PENDING[str(self.directory(record["result_id"]).resolve())] = deepcopy(record)

    def release_pending(self, result_id):
        with _PENDING_LOCK:
            _PENDING.pop(str(self.directory(result_id).resolve()), None)

    def list_records(self, target):
        records = []
        for path in self.root.glob("*/target.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                saved_target = record["target"]
                if not isinstance(saved_target, dict):
                    raise ValueError("上传成果目标无效。")
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.diagnostics.append(f"上传成果目标索引损坏，已隔离 {path.parent.name}: {type(exc).__name__}")
                continue
            if all(
                saved_target.get(key) == target.get(key)
                for key in (
                    "project_id",
                    "scope",
                    "endpoint",
                    "account_user_id",
                )
            ):
                record["receipts"] = [
                    json.loads(receipt.read_text(encoding="utf-8"))
                    for receipt in sorted(path.parent.glob("receipt-*.json"))
                ]
                records.append(record)
        return tuple(records)
