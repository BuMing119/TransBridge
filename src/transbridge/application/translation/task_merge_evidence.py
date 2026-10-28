"""Durable, selected-entry evidence captured before publishing AI results."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from threading import RLock

from .result_merge import ResultMergePlan
from .task_history import atomic_json


class TaskMergeEvidenceStore:
    """Append immutable merge generations without rewriting full project history."""

    def __init__(self, root: Path, task_id: str):
        self.root = Path(root) / "merge-evidence"
        self._task_token = sha256(task_id.encode()).hexdigest()[:16]
        self.task_id = task_id
        self._lock = RLock()
        self.error = ""
        self._pending_receipts = {}
        self._last_saved = None

    def save(self, candidates, plan, policy):
        def state(value):
            if value is None:
                return None
            return {**asdict(value), "entry_key": value.entry_key.to_dict()}

        payload = {
            "schema": 1,
            "task_id": self.task_id,
            "policy": str(policy),
            "candidates": [{"base": state(row.base), "incoming": state(row.incoming)} for row in candidates],
            "conflicts": [
                {
                    "entry_key": row.entry_key.to_dict(),
                    "reason": row.reason,
                    "decision": row.decision,
                    "base": state(row.base),
                    "current": state(row.current),
                    "incoming": state(row.incoming),
                }
                for row in plan.conflicts
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        path = self.root / f"{self._task_token}-{sha256(encoded.encode()).hexdigest()[:32]}.json"
        with self._lock:
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    if existing != payload:
                        raise ValueError("内容与地址不匹配")
                except (OSError, ValueError) as exc:
                    raise ValueError(f"合入恢复证据损坏，停止合入并保留原文件：{path.name}") from exc
            else:
                atomic_json(path, payload)
            # Only frozen merge payloads may be reused by the subsequent receipt.
            # Each new pre-commit save still verifies the addressed file above.
            self._last_saved = (
                (candidates, plan, policy, path)
                if isinstance(candidates, tuple) and isinstance(plan, ResultMergePlan)
                else None
            )
        return path

    def acknowledge(self, candidates, plan, policy):
        """Record actual applied keys only after the authoritative mutation succeeded.

        A receipt disk failure cannot undo that mutation or hide its outcome;
        retain its small payload for an explicit persistence retry instead.
        """
        try:
            with self._lock:
                saved = self._last_saved
                path = (
                    saved[3]
                    if saved is not None and saved[0] is candidates and saved[1] is plan and saved[2] == policy
                    else self.save(candidates, plan, policy)
                )
            receipt = {
                "schema": 1,
                "task_id": self.task_id,
                "evidence_file": path.name,
                "applied_keys": [row.entry_key.to_dict() for row in plan.applied],
            }
            receipt["sha256"] = sha256(
                json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            receipt_path = path.with_suffix(".applied.json")
            with self._lock:
                self._pending_receipts[receipt_path] = receipt
                self.retry_receipts()
        except (OSError, ValueError) as exc:
            self.error = f"合入已完成，但实际合入回执尚未保存：{exc}"
            return False
        return True

    def retry_receipts(self):
        with self._lock:
            for path, payload in tuple(self._pending_receipts.items()):
                atomic_json(path, payload)
                del self._pending_receipts[path]
            self.error = ""

    def load(self):
        """Expose original current-state evidence for explicit review and restoration."""
        records = []

        def presentation_order(path):
            receipt = path.with_suffix(".applied.json")
            # Reusing identical pre-write content creates a new successful
            # overwrite event when its actual receipt is published again.
            return (receipt.stat().st_mtime_ns if receipt.exists() else path.stat().st_mtime_ns, path.name)

        paths = sorted(
            (path for path in self.root.glob(f"{self._task_token}-*.json") if not path.name.endswith(".applied.json")),
            key=presentation_order,
        )
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
            expected = f"{self._task_token}-{sha256(encoded.encode()).hexdigest()[:32]}.json"
            if payload.get("schema") != 1 or payload.get("task_id") != self.task_id or path.name != expected:
                raise ValueError(f"合入恢复证据损坏，原文件已保留：{path.name}")
            receipt_path = path.with_suffix(".applied.json")
            applied = []
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                digest = receipt.pop("sha256", None)
                actual_digest = sha256(
                    json.dumps(receipt, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
                if (receipt.get("schema"), receipt.get("task_id"), receipt.get("evidence_file")) != (
                    1,
                    self.task_id,
                    path.name,
                ) or digest != actual_digest:
                    raise ValueError(f"实际合入回执无效，原文件已保留：{receipt_path.name}")
                applied = receipt["applied_keys"]
            payload["applied_keys"] = applied
            records.append(payload)
        return tuple(records)
