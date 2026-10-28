"""Validate and retain historical project-local AI outcomes as read-only assets."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.imported_task_history import IMPORT_INDEX, readonly_imported_report
from transbridge.application.translation.task_recovery import _validate as validate_recovery
from transbridge.application.translation.task_recovery_results import _digest, _validate as validate_results

from .legacy_project_records import LegacyProjectRecords
from .v2.models import SchemaValidationError
from .v2.schema import parse_json_bytes

_ROOTS = {"ai-task-history", "ai-task-recovery", "ai-proofread-resume", "ai-translation-resume", "download-results"}


def is_legacy_task_member(member: str) -> bool:
    parts = PurePosixPath(member).parts
    if not parts or parts[0] not in _ROOTS:
        return False
    if parts[0] == "ai-task-history":
        return len(parts) == 3 and parts[2].endswith(".json")
    if parts[0] == "download-results":
        return len(parts) == 3 and parts[-1].endswith(".json")
    if parts[0] == "ai-translation-resume":
        return len(parts) == 3 and parts[1] == "postprocess" and parts[-1].endswith(".json")
    if parts[0] == "ai-proofread-resume":
        return (
            len(parts) == 2
            or len(parts) == 4
            and parts[1] == "tasks"
            and len(parts[2]) == 64
            and all(char in "0123456789abcdef" for char in parts[2])
        ) and parts[-1].endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal"))
    if len(parts) == 3 and parts[1] == "locks":
        token = PurePosixPath(parts[-1]).stem
        # An old bundle may contain an inert transaction lock beside its
        # outcomes. Preserve it as an opaque asset, never as a live lock.
        return parts[-1].endswith(".lock") and len(token) == 16 and all(char in "0123456789abcdef" for char in token)
    return (len(parts) == 2 and parts[-1].endswith((".json", ".lease"))) or (
        len(parts) == 3 and parts[1] in {"results", "references", "merge-evidence"} and parts[-1].endswith(".json")
    )


def _capture_reports(records: LegacyProjectRecords) -> tuple[dict, ...]:
    members = sorted(member for member in records.members if is_legacy_task_member(member))
    if not members:
        return ()
    raw = {member: records.read(member) for member in members}
    documents = {member: parse_json_bytes(payload) for member, payload in raw.items() if member.endswith(".json")}
    reports, referenced, used_results = [], set(), set()
    for member, metadata in documents.items():
        parts = PurePosixPath(member).parts
        if parts[0] != "ai-task-history" or parts[-1] != "metadata.json":
            continue
        run_id, filename = metadata.get("run_id"), metadata.get("record_file")
        if not isinstance(run_id, str) or hashlib.sha256(run_id.encode()).hexdigest() != parts[1]:
            raise ValueError(f"旧 AI 历史文件身份不匹配：{member}")
        if not isinstance(filename, str) or PurePosixPath(filename).name != filename or "\\" in filename:
            raise ValueError(f"旧 AI 历史结果路径无效：{member}")
        payload_member = str(PurePosixPath(member).parent / filename)
        if payload_member not in documents:
            raise ValueError(f"旧 AI 历史缺少结果记录：{payload_member}")
        report = deepcopy(documents[payload_member])
        if report.get("run_id") != run_id:
            raise ValueError(f"旧 AI 历史结果任务身份不匹配：{payload_member}")
        report.update({key: value for key, value in metadata.items() if key not in {"sources", "record_file"}})
        reports.append(readonly_imported_report(report, member=payload_member))
        referenced.update((member, payload_member))
    for member, document in documents.items():
        parts = PurePosixPath(member).parts
        if parts[0] == "ai-task-history" and member not in referenced:
            reports.append(readonly_imported_report(document, member=member))
        elif parts[0] == "ai-task-recovery" and len(parts) == 2:
            record = validate_recovery(document)
            task_id = record["task_id"]
            token = hashlib.sha256(task_id.encode()).hexdigest()
            if parts[-1] != f"{token}.json":
                raise ValueError(f"旧 AI 恢复文件身份不匹配：{member}")
            result_member = f"ai-task-recovery/results/{token}.json"
            result = documents.get(result_member)
            rows = []
            if result is not None:
                payload = result.get("payload")
                if result.get("sha256") != _digest(payload):
                    raise ValueError(f"旧 AI 恢复结果校验和不匹配：{result_member}")
                rows = validate_results(payload, task_id)["rows"]
                used_results.add(result_member)
                scope = {
                    (source["key"], EntryKey.from_dict(key))
                    for source in record["sources"]
                    for key in (*source["translate_keys"], *source["polish_keys"])
                }
                if any((row["source"], EntryKey.from_dict(row["entry_key"])) not in scope for row in rows):
                    raise ValueError(f"旧 AI 恢复结果不属于原任务范围：{result_member}")
            sources = []
            for source in record["sources"]:
                inputs = {json.dumps(row["entry_key"], sort_keys=True): row for row in source.get("inputs", ())}
                entries = []
                for row in rows:
                    if row["source"] != source["key"]:
                        continue
                    before = row["before"]
                    entries.append({
                        "entry_key": row["entry_key"],
                        "original": before["original"],
                        "before": before["translation"],
                        "candidate": row["text"],
                        "before_revision": before["revision"],
                        "stage": row["stage"],
                        "context": before["context"],
                        "accepted": row["decision"] == "accepted",
                        "report_details": row["report_details"],
                    })
                    inputs.pop(json.dumps(row["entry_key"], sort_keys=True), None)
                entries.extend(
                    {
                        "entry_key": row["entry_key"],
                        "original": row["original"],
                        "before": row["translation"],
                        "candidate": "",
                        "before_revision": row["revision"],
                        "stage": row["stage"],
                        "context": row.get("context", ""),
                    }
                    for row in inputs.values()
                )
                sources.append({"key": source["key"], "label": source["label"], "snapshot": {"entries": entries}})
            reports.append(readonly_imported_report({**record, "sources": sources}, member=member))
    for member, document in documents.items():
        parts = PurePosixPath(member).parts
        if len(parts) != 3 or parts[:2] != ("ai-task-recovery", "results") or member in used_results:
            continue
        payload = document.get("payload")
        task_id = payload.get("task_id") if isinstance(payload, dict) else None
        if (
            not isinstance(task_id, str)
            or parts[-1] != f"{hashlib.sha256(task_id.encode()).hexdigest()}.json"
            or document.get("sha256") != _digest(payload)
        ):
            raise ValueError(f"旧 AI 无主恢复结果身份或校验和无效：{member}")
        rows = validate_results(payload, task_id)["rows"]
        sources = []
        for key in dict.fromkeys(row["source"] for row in rows):
            entries = [
                {
                    "entry_key": row["entry_key"],
                    "original": row["before"]["original"],
                    "before": row["before"]["translation"],
                    "candidate": row["text"],
                    "before_revision": row["before"]["revision"],
                    "stage": row["stage"],
                    "context": row["before"]["context"],
                    "accepted": row["decision"] == "accepted",
                    "report_details": row["report_details"],
                }
                for row in rows
                if row["source"] == key
            ]
            sources.append({"key": key, "label": key, "snapshot": {"entries": entries}})
        reports.append(
            readonly_imported_report(
                {"task_id": task_id, "sources": sources, "created_at": "", "state": "interrupted"}, member=member
            )
        )
    return tuple(reports)


class _TaskRecords:
    def __init__(self, payloads):
        self._payloads = payloads
        self.members = frozenset(payloads)

    def read(self, member):
        return self._payloads[member]


def capture_legacy_task_assets(records: LegacyProjectRecords, *, variant_names=()) -> tuple[tuple[str, bytes], ...]:
    """Retain recognized raw assets; content damage isolates only its own report."""
    raw = {}
    for member in sorted(records.members):
        parts = PurePosixPath(member).parts
        variant_record = parts[0] in variant_names and (
            len(parts) == 2 and parts[1] == "current.json" or len(parts) == 3 and parts[1] == "snapshots"
        )
        if is_legacy_task_member(member) and not variant_record:
            raw[member] = records.read(member)
    if not raw:
        return ()
    reports, diagnostics, handled = [], [], set()

    def capture(members):
        try:
            reports.extend(_capture_reports(_TaskRecords({member: raw[member] for member in members})))
        except (KeyError, TypeError, ValueError, SchemaValidationError) as exc:
            member = members[0]
            message = f"旧成果无法规范化，原始附件已完整保留，仅供导出：{member}；{exc}"
            diagnostics.append({"member": member, "message": message})
            reports.append(
                readonly_imported_report(
                    {
                        "run_id": "unreadable:" + hashlib.sha256(member.encode()).hexdigest(),
                        "created_at": "",
                        "state": "error",
                        "sources": [{"key": member, "label": "旧成果（仅保留原始附件）", "error": message}],
                    },
                    member=member,
                )
            )
            return False
        return True

    # Process each metadata generation independently. A damaged orphan cannot
    # prevent other complete reports from appearing in the read-only history.
    for member, payload in raw.items():
        parts = PurePosixPath(member).parts
        if parts[0] != "ai-task-history" or parts[-1] != "metadata.json":
            continue
        group = [member]
        try:
            filename = parse_json_bytes(payload).get("record_file")
            if isinstance(filename, str) and PurePosixPath(filename).name == filename and "\\" not in filename:
                target = str(PurePosixPath(member).parent / filename)
                if target in raw:
                    group.append(target)
        except (ValueError, SchemaValidationError):
            pass  # The content decoder below records a visible diagnostic.
        if capture(group):
            handled.update(group)
        else:
            handled.add(member)
    for member in raw:
        parts = PurePosixPath(member).parts
        if member in handled:
            continue
        if parts[0] == "ai-task-history":
            capture([member])
        elif parts[0] == "ai-task-recovery" and len(parts) == 2 and member.endswith(".json"):
            result = f"ai-task-recovery/results/{PurePosixPath(member).name}"
            group = [member, result] if result in raw else [member]
            if capture(group):
                handled.update(group)
    for member in raw:
        parts = PurePosixPath(member).parts
        if member not in handled and len(parts) == 3 and parts[:2] == ("ai-task-recovery", "results"):
            capture([member])
    for member, payload in raw.items():
        parts = PurePosixPath(member).parts
        supporting_json = parts[0] in {"download-results", "ai-translation-resume"} or (
            parts[0] == "ai-task-recovery" and len(parts) == 3 and parts[1] in {"references", "merge-evidence"}
        )
        if supporting_json:
            try:
                parse_json_bytes(payload)
            except (ValueError, SchemaValidationError) as exc:
                diagnostics.append({"member": member, "message": f"旧成果附件内容无法解析，原始字节已保留：{exc}"})
    assets, originals = [], []
    for member, payload in raw.items():
        digest = hashlib.sha256(payload).hexdigest()
        # Keep historical long hash/record paths in the index. Flat asset names
        # avoid adding their nesting to ordinary Windows persistence roots.
        # Opaque suffix prevents portable export from reopening SQLite or dropping
        # WAL/journal attachments; original filenames remain in the index.
        name = f"legacy-ai/{hashlib.sha256(member.encode()).hexdigest()[:24]}.bin"
        assets.append((name, payload))
        originals.append({"member": member, "asset": name, "sha256": digest})
    index = {"schema": 1, "records": reports, "original_files": originals, "diagnostics": diagnostics}
    assets.append((IMPORT_INDEX, json.dumps(index, ensure_ascii=False, allow_nan=False).encode("utf-8")))
    return tuple(assets)
