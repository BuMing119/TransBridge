"""Detached recovery initialization and updates shared by sync and async callers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from transbridge.application.io.identity import EntryKey
from transbridge.application.translation.proofread_checkpoint import proofread_settings
from transbridge.application.translation.task_config_snapshot import execution_snapshot


@dataclass(frozen=True)
class RecoverySource:
    key: str
    label: str
    translate_keys: tuple
    polish_keys: tuple
    entries: tuple = ()
    esp_path: str | None = None


@dataclass(frozen=True)
class RecoveryInitialization:
    task_id: str
    continuing: str | None
    identity: tuple[str, str]
    mode: str
    config: object  # A private copy, never the window's editable configuration.
    sources: tuple[RecoverySource, ...]
    overwrite: bool = False
    origin: dict | None = None
    reference_snapshot: dict | None = None
    conflict_policy: str = "keep_current"

    def execute(self, store):
        previous = store.load(self.continuing) if self.continuing else None
        origin = previous or self.origin or {}
        lease_task_id = origin.get("origin_task_id") or self.continuing or origin.get("task_id") or self.task_id
        store.acquire(lease_task_id)
        try:
            if previous is not None and store.load(self.continuing) != previous:
                raise ValueError("恢复任务记录在取得执行锁前已变化，请刷新后重试。")
            return self._execute(store, previous=previous)
        except Exception:
            store.release()
            raise

    def _execute(self, store, *, previous=None):
        from transbridge.application.translation.task_config_snapshot import execution_signature as signature

        from .source_terms import source_terminology_path
        from .task_recovery_binding import _STATES

        sources = [
            {
                "key": source.key,
                "label": source.label,
                "translate_keys": [key.to_dict() for key in source.translate_keys],
                "polish_keys": [key.to_dict() for key in source.polish_keys],
                "esp_path": source.esp_path,
                "checkpoint_stem": Path(source_terminology_path(source.esp_path, source.key)).stem,
                "inputs": [
                    {
                        "entry_key": entry.identity.to_dict(),
                        "original": entry.original,
                        "translation": entry.translation,
                        "stage": entry.stage,
                        "context": entry.context or "",
                        "revision": entry.revision.value,
                        "entry": entry.to_dict(),
                    }
                    for entry in source.entries
                ],
            }
            for source in self.sources
        ]
        original_sources = {source["key"]: source for source in (self.origin or {}).get("sources", ())}
        for source in sources:
            original = original_sources.get(source["key"], {})
            stems = original.get("checkpoint_stems") or [original.get("checkpoint_stem")]
            source["checkpoint_stems"] = list(dict.fromkeys(filter(None, (*stems, source["checkpoint_stem"]))))
        expected = dict(
            project_id=self.identity[0],
            variant_id=self.identity[1],
            mode=self.mode,
            config_digest=signature(self.config),
        )
        if not self.continuing:
            executions = list((self.origin or {}).get("execution_runs", ()))
            if self.origin and self.origin.get("legacy"):
                executions.append(self.origin["task_id"])
            executions.append(self.task_id)
            execution_inputs = dict((self.origin or {}).get("execution_inputs", {}))
            if self.origin:
                for execution in executions:
                    if execution != self.task_id:
                        execution_inputs.setdefault(execution, self.origin["sources"])
            execution_inputs[self.task_id] = sources
            references = dict((self.origin or {}).get("execution_references", {}))
            if self.reference_snapshot is not None:
                references[self.task_id] = self.reference_snapshot
            return store.create({
                "task_id": self.task_id,
                "created_at": datetime.now().astimezone().isoformat(),
                **expected,
                "checkpoint_settings": proofread_settings(self.config),
                "execution_config": execution_snapshot(self.config),
                "conflict_policy": self.conflict_policy,
                "sources": sources,
                "state": "preparing",
                "applied_entries": [],
                "supported": True,
                "recovery_contract": 2,
                "overwrite": self.overwrite,
                "execution_runs": list(dict.fromkeys(executions)),
                "execution_inputs": execution_inputs,
                **({"reference_snapshot": self.reference_snapshot} if self.reference_snapshot is not None else {}),
                **({"execution_references": references} if references else {}),
                **(
                    {"origin_task_id": self.origin.get("origin_task_id") or self.origin["task_id"]}
                    if self.origin
                    else {}
                ),
            })
        record = previous
        if record["state"] not in _STATES or not record.get("supported", False):
            raise ValueError("原任务恢复状态无效或不受支持。")
        if any(record.get(key) != value for key, value in expected.items()):
            raise ValueError("恢复任务身份或配置不匹配，不能覆盖原任务记录。")
        if record.get("recovery_contract") == 2 and record.get("overwrite", False) != self.overwrite:
            raise ValueError("恢复任务的覆盖选项与原记录不匹配。")
        known = {source["key"]: source for source in record["sources"]}
        for source in sources:
            old = known.get(source["key"])
            if old is None or any(
                not {EntryKey.from_dict(key) for key in source[field]}
                <= {EntryKey.from_dict(key) for key in old[field]}
                for field in ("translate_keys", "polish_keys")
            ):
                raise ValueError("恢复任务范围与原记录不匹配。")
        executions = list(dict.fromkeys((*record.get("execution_runs", (record["task_id"],)), self.task_id)))
        execution_inputs = dict(record.get("execution_inputs", {record["task_id"]: record["sources"]}))
        execution_inputs[self.task_id] = sources
        from transbridge.application.translation.task_result_recovery import load_recovery_results

        results = load_recovery_results(store, record)
        references = dict(record.get("execution_references", {}))
        if self.reference_snapshot is not None:
            references[self.task_id] = self.reference_snapshot
        metadata = store.update_metadata(
            self.continuing,
            execution_runs=executions,
            execution_inputs=execution_inputs,
            **({"execution_references": references} if references else {}),
        )
        return {**record, **metadata, "results": results}


@dataclass(frozen=True)
class RecoveryUpdate:
    sequence: int
    state: str
    project_saved: bool
    entries: object
    saved_entries: object | None = None

    def execute(self, store, task_id):
        from transbridge.application.translation.task_result_recovery import save_recovery_results

        save_recovery_results(store, task_id, self.entries)
        # Read the last durable generation in the serialized worker. Coalescing
        # updates must never discard evidence from an earlier successful save.
        saved = self.saved_entries
        saved_rows = {}
        for key in saved.applied_keys if saved is not None else ():
            row = saved.entries[key]
            saved_rows[key] = {
                "entry_key": key.to_dict(),
                "original": row.before.original,
                "text": row.text,
                "stage": row.stage,
                "report_details": {
                    name: value
                    for name, value in getattr(row, "report_details", {}).items()
                    if name
                    in {
                        "review_status",
                        "review_reason",
                        "detected_issues",
                        "review_before_text",
                        "review_contract_version",
                    }
                },
            }

        def change(previous):
            fields = dict(state=self.state, project_saved=self.project_saved)
            if saved_rows:
                applied = {EntryKey.from_dict(row["entry_key"]): row for row in previous.get("applied_entries", ())}
                applied.update(saved_rows)
                fields["applied_entries"] = list(applied.values())
            fields["applied"] = bool(previous.get("applied") or saved_rows or self.entries.applied_keys)
            return fields

        return store.mutate_metadata(task_id, change, load_fields=("applied_entries",) if saved_rows else ())
