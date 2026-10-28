"""Read imported AI reports without granting their historical owners write access."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from transbridge.persistence.v2.ids import ProjectId
from transbridge.persistence.v2.schema import parse_json_bytes

from .task_history import TaskHistoryStore, restore_report_snapshot

IMPORT_INDEX = "legacy-ai/index.json"


def readonly_imported_report(record: dict, *, member: str) -> dict:
    """Normalize display/export fields while retaining original ownership as evidence."""
    result = deepcopy(record)
    run_id = result.get("run_id") or result.get("task_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError(f"旧 AI 成果缺少任务身份：{member}")
    sources = result.get("sources")
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        raise ValueError(f"旧 AI 成果缺少来源记录：{member}")
    for source in sources:
        if any(not isinstance(source.get(field), str) for field in ("key", "label")):
            raise ValueError(f"旧 AI 成果来源身份无效：{member}")
        snapshot = source.setdefault("snapshot", {})
        rows = snapshot.setdefault("entries", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"旧 AI 成果词条无效：{member}")
        for row in rows:
            row.setdefault("before_revision", 0)
            row.setdefault("before", "")
            row.setdefault("candidate", "")
            row.setdefault("stage", 0)
            row.setdefault("accepted", False)
            row.setdefault("report_details", {})
        snapshot.setdefault("schema", "transbridge.postprocess-report.v1")
        snapshot.setdefault("run_id", run_id)
        snapshot.setdefault("outcome", "completed")
        snapshot.setdefault("counts", {"input": len(rows), "accepted": sum(bool(row["accepted"]) for row in rows)})
        snapshot.setdefault("diagnostics", [])
        # The existing report contract is also the export validation boundary.
        restore_report_snapshot(snapshot)
        source.setdefault("error", "旧工程导入成果：原任务归属未经验证，仅供查看和导出。")
    result.update(
        run_id=run_id,
        project_id=None,
        variant_id=None,
        supported=False,
        applied=False,
        saved=False,
        project_saved=False,
        legacy_import={
            "member": member,
            "run_id": run_id,
            "project_id": record.get("project_id"),
            "variant_id": record.get("variant_id"),
        },
    )
    return result


class ProjectTaskHistoryReader:
    """Combine native history and the captured project's imported read-only index."""

    def __init__(self, directory: Path, project_id: str | None):
        self._native = TaskHistoryStore(Path(directory) / "ai-task-history")
        self._project_id = project_id
        self._index = None if project_id is None else Path(directory) / ProjectId(project_id).encoded / IMPORT_INDEX

    def _imported(self) -> tuple[dict, ...]:
        if self._index is None or not self._index.is_file():
            return ()
        document = parse_json_bytes(self._index.read_bytes())
        if document.get("schema") != 1 or not isinstance(document.get("records"), list):
            raise ValueError("导入的 AI 成果索引无效，原文件已保留。")
        records = []
        for index, record in enumerate(document["records"]):
            if not isinstance(record, dict) or not isinstance(record.get("legacy_import"), dict):
                raise ValueError("导入的 AI 成果索引缺少来源证据。")
            value = readonly_imported_report(record, member=record["legacy_import"]["member"])
            value["legacy_import"] = deepcopy(record["legacy_import"])
            value["run_id"] = f"imported:{self._project_id}:{index}:{record['run_id']}"
            records.append(value)
        return tuple(records)

    def list_records(self) -> tuple[dict, ...]:
        return tuple(
            sorted(
                (*self._native.list_records(), *self._imported()),
                key=lambda row: row.get("created_at", ""),
                reverse=True,
            )
        )

    def load(self, run_id: str) -> dict:
        if self._project_id is not None and run_id.startswith(f"imported:{self._project_id}:"):
            for record in self._imported():
                if record["run_id"] == run_id:
                    return record
            raise ValueError("导入的 AI 成果记录不存在。")
        return self._native.load(run_id)
