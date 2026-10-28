"""Checksummed atomic final results, separate from raw translation candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from transbridge.application.io.identity import EntryKey

from .task_history import atomic_json


def _digest(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _validate(payload, task_id):
    if not isinstance(payload, dict) or payload.get("schema") != 1 or payload.get("task_id") != task_id:
        raise ValueError("AI 恢复结果的任务身份或格式无效")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("AI 恢复结果缺少词条列表")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("AI 恢复结果词条无效")
        key = EntryKey.from_dict(row["entry_key"])
        if key in seen:
            raise ValueError("AI 恢复结果包含重复词条")
        seen.add(key)
        before = row["before"]
        if (
            not isinstance(row.get("source"), str)
            or row.get("action") not in {"translation", "polish", "postprocess"}
            or row.get("status") != "succeeded"
            or row.get("decision") not in {"pending", "accepted", "rejected"}
            or not isinstance(row.get("text"), str)
            or not row["text"].strip()
            or type(row.get("stage")) is not int
            or type(row.get("applied")) is not bool
            or type(row.get("attempt")) is not int
            or not isinstance(row.get("reason"), str)
            or not isinstance(row.get("report_details"), dict)
            or not isinstance(before, dict)
            or any(not isinstance(before.get(field), str) for field in ("original", "translation", "context"))
            or type(before.get("stage")) is not int
            or type(before.get("revision")) is not int
        ):
            raise ValueError("AI 恢复结果缺少完整的输入、处理结果或用户决定")
    return payload


class TaskRecoveryResults:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, task_id):
        return self.root / "results" / f"{hashlib.sha256(task_id.encode()).hexdigest()}.json"

    def load(self, task_id):
        path = self._path(task_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise ValueError(f"无法读取 AI 恢复结果 {path.name}：{exc}") from exc
        try:
            payload = value["payload"]
            if value["sha256"] != _digest(payload):
                raise ValueError("校验和不匹配")
            return _validate(payload, task_id)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"AI 恢复结果损坏，原文件已保留：{path.name}：{exc}") from exc

    def merge(self, task_id, rows):
        if not rows:
            return
        previous = self.load(task_id)
        combined = {EntryKey.from_dict(row["entry_key"]): row for row in (previous or {}).get("rows", ())}
        for row in rows:
            key = EntryKey.from_dict(row["entry_key"])
            old = combined.get(key)
            if old is not None:
                if old["attempt"] > row["attempt"]:
                    continue
                if old["attempt"] == row["attempt"] and old.get("decision_revision", 0) > row.get(
                    "decision_revision", 0
                ):
                    continue
                if (
                    old["attempt"] == row["attempt"]
                    and old["text"] == row["text"]
                    and old["decision"] != "pending"
                    and row["decision"] == "pending"
                ):
                    continue  # A delayed state write must not undo the user's decision.
            combined[key] = row
        payload = _validate({"schema": 1, "task_id": task_id, "rows": list(combined.values())}, task_id)
        if payload != previous:
            atomic_json(self._path(task_id), {"payload": payload, "sha256": _digest(payload)})
