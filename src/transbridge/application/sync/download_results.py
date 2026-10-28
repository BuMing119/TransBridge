"""Durable source-sized download results, independent of a running GUI session."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
import json
import logging
from pathlib import Path
from stat import S_ISDIR
from uuid import uuid4

from transbridge.application.io.identity import EntryKey, EntryRevision, ExternalEntryRef, SourceNamespace
from transbridge.application.translation.result_merge import ResultConflictPolicy, ResultEntryChange, ResultEntryState
from transbridge.application.translation.task_history import atomic_json

from .batch_download import BatchDownloadSource
from .models import LocalEntrySnapshot, RemoteEntrySnapshot

_logger = logging.getLogger(__name__)


class DownloadResultRecords(tuple):
    """Tuple-compatible discovery result with diagnostics for preserved bad records."""

    def __new__(cls, records=(), *, diagnostics=()):
        instance = super().__new__(cls, records)
        instance.diagnostics = tuple(diagnostics)
        return instance


class DownloadResultStore:
    """Each completed source is atomic; retries replace only that source payload."""

    def __init__(self, root):
        self.root = Path(root) / "download-results"

    def create(self, target, policy):
        record = {
            "schema": 1,
            "result_id": uuid4().hex,
            "created_at": datetime.now(UTC).isoformat(),
            "target": list(target),
            "policy": str(policy),
            "sources": {},
            "decisions": [],
        }
        self.save(record)
        return record

    def directory(self, result_id):
        return self.root / sha256(result_id.encode()).hexdigest()[:24]

    def save(self, record):
        atomic_json(self.directory(record["result_id"]) / "result.json", record)

    def _payload_path(self, record, filename):
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".json"):
            raise ValueError("下载成果文件名无效。")
        return self.directory(record["result_id"]) / filename

    def save_source(self, record, scope, read, source):
        payload = _source_payload(scope, read, source)
        filename = sha256(scope.source_id.encode()).hexdigest() + ".json"
        atomic_json(self.directory(record["result_id"]) / filename, payload)
        record["sources"][scope.source_id] = filename
        self.save(record)

    def list_records(self, target):
        records, diagnostics = [], []
        try:
            directories = tuple(self.root.iterdir())
        except FileNotFoundError:
            return DownloadResultRecords()
        for directory in directories:
            if not S_ISDIR(directory.stat().st_mode):
                continue
            path = directory / "result.json"
            try:
                record = _read(path)
                self._validate_record(record, path)
            except FileNotFoundError:
                continue  # A source payload may precede its record during an atomic save.
            except (ValueError, UnicodeError) as error:
                message = f"下载成果记录已跳过，原文件保留：{path}（{error}）"
                diagnostics.append(message)
                _logger.warning("%s", message)
                continue
            if record["target"] == list(target):
                records.append(record)
        return DownloadResultRecords(
            sorted(records, key=lambda item: item["created_at"], reverse=True), diagnostics=diagnostics
        )

    def _validate_record(self, record, path):
        if type(record.get("schema")) is not int or record["schema"] != 1:
            raise ValueError("不支持的下载成果版本。")
        if not isinstance(record.get("result_id"), str) or not record["result_id"]:
            raise ValueError("下载成果缺少有效标识。")
        if self.directory(record["result_id"]) != path.parent:
            raise ValueError("下载成果标识与存储位置不一致。")
        if not isinstance(record.get("created_at"), str) or not record["created_at"]:
            raise ValueError("下载成果缺少创建时间。")
        datetime.fromisoformat(record["created_at"])
        if (
            not isinstance(record.get("target"), list)
            or len(record["target"]) != 5
            or any(isinstance(value, (list, dict)) for value in record["target"])
        ):
            raise ValueError("下载成果目标无效。")
        if not isinstance(record.get("policy"), str):
            raise ValueError("下载成果处理策略无效。")
        ResultConflictPolicy(record["policy"])
        if not isinstance(record.get("sources"), dict):
            raise ValueError("下载成果来源索引无效。")
        for source_id, filename in record["sources"].items():
            if not source_id:
                raise ValueError("下载成果来源标识无效。")
            self._payload_path(record, filename)
        for field in ("merges", "merge_attempts"):
            if field in record:
                if not isinstance(record[field], list):
                    raise ValueError("下载成果合入索引无效。")
                for filename in record[field]:
                    self._payload_path(record, filename)
        decisions = record.get("decisions", [])
        if not isinstance(decisions, list) or any(
            not isinstance(row, list) or len(row) != 2 or not all(isinstance(value, str) for value in row)
            for row in decisions
        ):
            raise ValueError("下载成果选择记录无效。")

    def sources(self, record, *, scope_factory, read_factory):
        result = []
        for source_id, filename in record["sources"].items():
            payload = _read(self._payload_path(record, filename))
            raw = payload["scope"]
            scope = scope_factory(
                source_id,
                SourceNamespace(raw["namespace"]),
                tuple(raw["paths"]),
                tuple(raw["legacy_paths"]),
                tuple(raw["blocked_legacy_paths"]),
            )
            remote = tuple(_decode(row, remote=True) for row in payload["remote"])
            source = BatchDownloadSource(
                source_id, payload["label"], tuple(_decode(row) for row in payload["base"]), remote
            )
            read = read_factory(source_id, remote, tuple(payload["read"]["paths"]), payload["read"]["error"])
            result.append((scope, read, source))
        return tuple(result)

    def export(self, record, path, *, source_overrides=()):
        sources = {source_id: _read(self._payload_path(record, name)) for source_id, name in record["sources"].items()}
        for scope, read, source in source_overrides:
            sources[scope.source_id] = _source_payload(scope, read, source)
        merges = [_read(self._payload_path(record, name)) for name in record.get("merges", ())]
        attempts = [_read(self._payload_path(record, name)) for name in record.get("merge_attempts", ())]
        atomic_json(
            Path(path),
            {
                **record,
                "source_results": list(sources.values()),
                "merge_evidence": merges,
                "merge_attempt_evidence": attempts,
            },
        )

    def save_merge(self, record, candidates, plan, policy):
        changes = {row.incoming.entry_key: row for row in candidates}
        conflicts = {row.entry_key: row for row in plan.conflicts}

        def state(row):
            return {**asdict(row), "entry_key": row.entry_key.to_dict()} if row else None

        payload = {
            "policy": str(policy),
            "applied": [
                {
                    "before": state(
                        conflicts[row.entry_key].current if row.entry_key in conflicts else changes[row.entry_key].base
                    ),
                    "after": state(row),
                }
                for row in plan.applied
            ],
            "conflicts": [
                {
                    "base": state(row.base),
                    "current": state(row.current),
                    "incoming": state(row.incoming),
                    "reason": row.reason,
                    "decision": row.decision,
                }
                for row in plan.conflicts
            ],
        }
        digest = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        filename = "merge-" + digest + ".json"
        atomic_json(self.directory(record["result_id"]) / filename, payload)
        record["merge_attempts"] = [name for name in record.get("merge_attempts", ()) if name != filename] + [filename]
        self.save(record)
        return filename

    def confirm_merge(self, record, filename):
        if filename not in record.get("merge_attempts", ()):
            raise ValueError("合入缺少覆盖前证据，不能确认处理回执。")
        record["merges"] = [name for name in record.get("merges", ()) if name != filename] + [filename]
        self.save(record)

    def reverse_changes(self, record):
        for filename in reversed(record.get("merges", ())):
            payload = _read(self._payload_path(record, filename))
            if not payload["applied"]:
                continue

            def state(raw):
                return ResultEntryState(**{**raw, "entry_key": EntryKey.from_dict(raw["entry_key"])})

            return tuple(ResultEntryChange(state(row["after"]), state(row["before"])) for row in payload["applied"])
        raise ValueError("本轮成果尚无覆盖前译文可恢复。")


def _source_payload(scope, read, source):
    return {
        "scope": {**asdict(scope), "namespace": scope.namespace.value},
        "read": {"paths": list(read.paths), "error": read.error},
        "label": source.label,
        "base": [_encode(entry) for entry in source.local_entries],
        "remote": [_encode(entry) for entry in read.entries],
    }


def _read(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"下载成果记录无效：{path}")
    return value


def _encode(entry):
    row = asdict(entry)
    row["entry_key"] = entry.entry_key.to_dict()
    row["external_ref"] = entry.external_ref.to_dict() if entry.external_ref else None
    if hasattr(entry, "revision"):
        row["revision"] = entry.revision.value
    return row


def _decode(row, *, remote=False):
    row = dict(row)
    row["entry_key"] = EntryKey.from_dict(row["entry_key"])
    row["external_ref"] = ExternalEntryRef.from_dict(row["external_ref"]) if row["external_ref"] else None
    if not remote:
        row["revision"] = EntryRevision(row["revision"])
    return (RemoteEntrySnapshot if remote else LocalEntrySnapshot)(**row)
