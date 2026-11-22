# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Read archived entry-upload receipts for the desktop task history."""

from __future__ import annotations

import json
from pathlib import Path
import shutil


class EntryUploadResultStore:
    """Access historical receipts through the existing task history operations."""

    def __init__(self, root):
        self.root = Path(root) / "entry-upload-results"

    def directory(self, result_id):
        if (
            not isinstance(result_id, str)
            or len(result_id) != 32
            or any(c not in "0123456789abcdef" for c in result_id)
        ):
            raise ValueError("上传成果 ID 无效。")
        return self.root / result_id

    def load(self, result_id):
        directory = self.directory(result_id)
        header = json.loads((directory / "target.json").read_text(encoding="utf-8"))
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

    def contains(self, result_id):
        try:
            return (self.directory(result_id) / "target.json").is_file()
        except ValueError:
            return False

    def history(self):
        records = []
        for path in self.root.glob("*/target.json"):
            header = json.loads(path.read_text(encoding="utf-8"))
            target = header["target"]
            summary = path.parent / "summary.json"
            state = json.loads(summary.read_text(encoding="utf-8"))["state"] if summary.exists() else "running"
            project_id, variant_id = target["project_identity"]
            records.append({
                "task_id": header["result_id"],
                "created_at": header["created_at"],
                "project_id": project_id,
                "variant_id": variant_id,
                "state": state,
                "mode": "upload",
                "supported": False,
                "sources": [{"key": "paratranz-upload", "label": "ParaTranz 上传"}],
                "result_kind": "upload",
            })
        return tuple(records)

    def delete_completed(self, result_id):
        record = self.load(result_id)
        if record["state"] != "completed" or any(
            row["outcome"] in {"in_flight", "unknown", "unverified"} for row in record["receipts"]
        ):
            raise ValueError("上传仍在执行、等待本地关联或需要先核对远端结果，暂不能删除回执。")
        directory = self.directory(result_id)
        if directory.is_symlink() or directory.resolve().parent != self.root.resolve():
            raise ValueError("上传回执目录超出任务存储范围。")
        shutil.rmtree(directory)
