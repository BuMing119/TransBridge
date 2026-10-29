"""Durable assistant task inputs and results over the shared AI task recovery store."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.application.preflight.inputs import SOURCE_CONTEXT_KNOWN
from transbridge.application.translation.result_merge import ResultConflictPolicy
from transbridge.application.translation.task_config_snapshot import execution_signature, execution_snapshot
from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.application.translation.task_merge_evidence import TaskMergeEvidenceStore
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.application.translation.task_result_recovery import (
    load_recovery_results,
    save_recovery_results,
    source_context_known,
)
from transbridge.application.translation.task_scope import SourceTask
from transbridge.config.task_results import TaskResultPreferences
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

_RUN_LOCK = RLock()
_PENDING = {}
_LOCATIONS = {}
_MERGE_PENDING = {}


def _caller(ctx):
    app = getattr(ctx, "app_context", None) or ctx
    context = getattr(ctx, "request_context", None) or getattr(app, "runtime_context", None)
    if not isinstance(context, RequestContext):
        raise ValueError("任务成果查询缺少调用者身份。")
    return app, context


def task_ports(ctx):
    app, context = _caller(ctx)
    identity = getattr(app, "active_version_identity", None)
    commands = getattr(app, "project_commands", None)
    if identity is None or commands is None or not isinstance(context, RequestContext):
        raise ValueError("权威工程版本或任务结果保存服务不可用，请先打开工程。")
    captured = getattr(ctx, "_target_version_identity", identity)
    if captured != identity:
        raise ValueError("工具调用对应的工程版本已切换，任务未启动。")
    context = replace(context, project_id=identity[0], variant_id=identity[1])
    root = Path(commands.task_result_directory(context)) / "ai-task-recovery"
    return app, commands, context, identity, root


def _authorized(record, context):
    if (
        record.get("origin") != "smart-assistant"
        or record.get("owner_id") != context.owner_id
        or record.get("session_id") != context.session_id
    ):
        raise ValueError("成果不属于当前调用者会话。")


def load_task_result(ctx, result_id):
    from .entry_task_results import EntryResultStore, pending_result

    app, context = _caller(ctx)
    entry_pending = pending_result(ctx, result_id)
    if entry_pending is not None:
        entry_pending.retry_save()
    with _RUN_LOCK:
        location = _LOCATIONS.get(result_id)
        pending = _PENDING.get(result_id)
    if location is not None:
        root, owner_id, session_id = location
        if (owner_id, session_id) != (context.owner_id, context.session_id):
            raise ValueError("成果不属于当前调用者会话。")
    else:
        root = Path(app.project_commands.task_result_directory(context)) / "ai-task-recovery"
    if pending is not None and pending.persistence_error:
        pending.retry_save()
    store = TaskRecoveryStore(root)
    record = store.load(result_id)
    _authorized(record, context)
    if record.get("result_kind"):
        store = EntryResultStore(root)
        if record["result_kind"] == "source":
            from ._source_import import retry_source_receipt

            retry_source_receipt(store, record)
    with _RUN_LOCK:
        receipt = _MERGE_PENDING.get(result_id)
    if receipt is not None:
        evidence, summary, rows = receipt
        try:
            evidence.retry_receipts()
            summary = {**summary, "receipt_saved": True}
            store.save_results(result_id, rows)
            record.update(store.update_metadata(result_id, merge=summary, merge_error=""))
        except (OSError, ValueError):
            pass  # The original successful merge and its retry payload remain reachable.
        else:
            with _RUN_LOCK:
                _MERGE_PENDING.pop(result_id, None)
    payload = store.load_results(result_id) if record.get("result_kind") else load_recovery_results(store, record)
    return store, record, payload


def result_view(ctx, result_id=None):
    if not result_id:
        app, context = _caller(ctx)
        root = Path(app.project_commands.task_result_directory(context)) / "ai-task-recovery"
        records = []
        for record in TaskRecoveryStore(root).list_summaries():
            try:
                _authorized(record, context)
            except ValueError:
                continue
            records.append({
                "result_id": record["task_id"],
                "state": record["state"],
                "mode": record["mode"],
                "created_at": record["created_at"],
                "merge": record.get("merge", {}),
                "error": record.get("error", ""),
            })
        with _RUN_LOCK:
            pending = tuple(_PENDING.values())
        indexed = {row["result_id"]: row for row in records}
        from .entry_task_results import pending_results

        for task in pending_results(ctx):
            indexed[task.result_id] = {
                "result_id": task.result_id,
                "state": "persistence_failed" if task.persistence_error else task.record["state"],
                "mode": task.record["mode"],
                "created_at": task.record["created_at"],
                "error": task.persistence_error,
            }
        for task in pending:
            try:
                _authorized(task.record, context)
            except ValueError:
                continue
            if task.persistence_error or task.result_id not in indexed:
                indexed[task.result_id] = {
                    "result_id": task.result_id,
                    "state": "persistence_failed" if task.persistence_error else task.pending_state,
                    "mode": task.record["mode"],
                    "created_at": task.record["created_at"],
                    "error": task.persistence_error,
                }
        return {"results": list(indexed.values())}
    try:
        store, record, payload = load_task_result(ctx, result_id)
    except (OSError, ValueError):
        from .entry_task_results import pending_result

        entry_pending = pending_result(ctx, result_id)
        if entry_pending is not None:
            return {
                **entry_pending.record,
                "result_id": result_id,
                "durable": False,
                "error": entry_pending.persistence_error,
            }
        with _RUN_LOCK:
            pending = _PENDING.get(result_id)
        if pending is None:
            raise
        _app, context = _caller(ctx)
        _authorized(pending.record, context)
        return {
            "result_id": result_id,
            "state": "persistence_failed" if pending.persistence_error else pending.pending_state,
            "durable": False,
            "error": pending.persistence_error,
            "execution": [
                {"entry_key": key.to_dict(), "status": row.status, "text": row.text, "reason": row.reason}
                for key, row in pending.ledger.entries.items()
            ],
        }
    return {
        "result_id": result_id,
        "state": record["state"],
        "mode": record["mode"],
        "execution": record.get("entry_statuses", []),
        "results": (payload or {}).get("rows", []),
        "merge": record.get("merge", {}),
        "merge_error": record.get("merge_error", ""),
        "error": record.get("error", ""),
        **(
            {
                "entries": record.get("entries", []),
                "unmapped": record.get("unmapped", []),
                "result_kind": record["result_kind"],
                "parse_outcome": record.get("parse_outcome"),
                "parse_diagnostics": record.get("parse_diagnostics", []),
                "raw_entries": record.get("raw_entries", []),
                "input_snapshot": record.get("input_snapshot") or record.get("prepared_source"),
            }
            if record.get("result_kind")
            else {}
        ),
        "merge_evidence": TaskMergeEvidenceStore(store.root, result_id).load(),
    }


class AssistantTaskResults:
    """Own a single detached run and persist its validated ledger before any live write."""

    @classmethod
    def capture(cls, ctx, collection, targets, config, terminology, *, mode, request_options=None):
        """Capture on the caller's dispatch thread; prepare performs all durable writes later."""
        return cls(
            ctx,
            collection,
            targets,
            config,
            terminology,
            mode=mode,
            defer_persistence=True,
            request_options=request_options,
        )

    def __init__(
        self,
        ctx,
        collection,
        targets,
        config,
        terminology,
        *,
        mode,
        defer_persistence=False,
        request_options=None,
    ):
        self.ctx = ctx
        self.app, self.commands, self.context, self.identity, root = task_ports(ctx)
        self.result_id = uuid4().hex
        self.store = TaskRecoveryStore(root)
        self.evidence = TaskMergeEvidenceStore(root, self.result_id)
        provider = getattr(self.app, "task_result_preferences_provider", None) or TaskResultPreferences
        self.policy = ResultConflictPolicy(provider().ai_policy)
        self.config = config.copy_for_execution() if hasattr(config, "copy_for_execution") else deepcopy(config)
        self.terminology = terminology
        from .task_source_identity import capture_source_identity

        source_identity = capture_source_identity(ctx, collection)
        self.esp_path = source_identity.resource_path
        self.report_context = source_identity.report_context()
        from .input_prerequisites import source_slot_for

        def captured_entry(entry):
            slot = source_slot_for(ctx, entry)
            if slot is not None and getattr(slot, "source_snapshot", None) is None and entry.context is None:
                return replace(entry, metadata=tuple({**dict(entry.metadata), SOURCE_CONTEXT_KNOWN: False}.items()))
            return entry

        detached = TranslationEntryCollection(captured_entry(entry) for entry in deepcopy(tuple(collection)))
        entries = tuple(detached.get(entry.identity) for entry in targets)
        translate_entries, polish_entries, postprocess_entries = entries, (), ()
        if mode == "polish":
            translate_entries, polish_entries = (), entries
        elif mode == "postprocess":
            translate_entries, postprocess_entries = (), entries
        elif mode == "mixed":
            from transbridge.application.translation.entry_alias import ai_entry_id
            from transbridge.paratranz.config_manager import apply_rules

            actions = apply_rules(self.config.action_rules, entries)
            translate_entries = tuple(
                entry
                for entry in entries
                if actions.get(ai_entry_id(entry)) == "translate" and (not entry.translation or entry.stage == 0)
            )
            polish_entries = tuple(
                entry for entry in entries if actions.get(ai_entry_id(entry)) == "polish" and entry.translation
            )
        source = source_identity.key
        self.task = SourceTask(
            source,
            source,
            self.esp_path,
            detached,
            translate_entries,
            polish_entries,
            postprocess_entries,
        )
        if not self.task.entries:
            raise ValueError("本次作用域没有可处理的词条。")
        self.ledger = TaskEntryResults((self.task,), self.result_id)
        snapshot = execution_snapshot(self.config)
        inputs = [
            {
                "entry_key": entry.identity.to_dict(),
                "original": entry.original,
                "translation": entry.translation,
                "stage": entry.stage,
                "context": entry.context or "",
                "context_known": source_context_known(entry),
                "revision": entry.revision.value,
                "entry": entry.to_dict(),
            }
            for entry in self.task.entries
        ]
        sources = [
            {
                "key": source,
                "label": source,
                "esp_path": self.esp_path,
                "translate_keys": [entry.identity.to_dict() for entry in self.task.translate_entries],
                "polish_keys": [entry.identity.to_dict() for entry in self.task.polish_entries],
                "postprocess_keys": [entry.identity.to_dict() for entry in self.task.postprocess_entries],
                "inputs": inputs,
            }
        ]
        reference = getattr(terminology, "snapshot_ref", None)
        self.record = {
            "task_id": self.result_id,
            "origin": "smart-assistant",
            "owner_id": self.context.owner_id,
            "session_id": self.context.session_id,
            "project_id": self.identity[0],
            "variant_id": self.identity[1],
            "mode": mode,
            "created_at": datetime.now(UTC).isoformat(),
            "config_digest": execution_signature(self.config),
            "execution_config": snapshot,
            "state": "preparing",
            "supported": False,
            "results_only": True,
            "recovery_error": "助手成果请用 get_task_result 查询、merge_task_result 合入；此处不重新调用 AI。",
            "recovery_contract": 2,
            "overwrite": False,
            "execution_runs": [self.result_id],
            "execution_inputs": {self.result_id: sources},
            "sources": sources,
            "conflict_policy": self.policy.value,
            "request_options": request_options or {},
            **({"reference_snapshot": reference.to_dict()} if reference is not None else {}),
        }
        self.prepared = False
        self._write_lock = RLock()
        self.pending_state, self.persistence_error = "preparing", ""
        self.pending_error = ""
        with _RUN_LOCK:
            _LOCATIONS[self.result_id] = (root, self.context.owner_id, self.context.session_id)
            _PENDING[self.result_id] = self
        if not defer_persistence:
            self.prepare(acquire=False)

    def prepare(self, *, acquire=True):
        with self._write_lock:
            self._prepare(acquire=acquire)

    def _prepare(self, *, acquire):
        if acquire:
            self.store.acquire(self.result_id)
        if self.prepared:
            return
        try:
            self.record = self.store.create(self.record)
        except Exception as exc:
            self.persistence_error = str(exc)
            raise
        self.prepared = True

    def persist(self, *, state, error=""):
        with self._write_lock:
            self.pending_state, self.pending_error = state, error
            with _RUN_LOCK:
                _PENDING[self.result_id] = self
            self.retry_save()

    def retry_save(self):
        with self._write_lock:
            self._retry_save()

    def _retry_save(self):
        try:
            if not self.prepared:
                self.prepare(acquire=False)
            save_recovery_results(self.store, self.result_id, self.ledger)
            metadata = self.store.update_metadata(
                self.result_id,
                state=self.pending_state,
                error=self.pending_error,
                entry_statuses=[
                    {"entry_key": key.to_dict(), "status": row.status, "reason": row.reason, "attempt": row.attempt}
                    for key, row in self.ledger.entries.items()
                ],
            )
            self.record.update(metadata)
        except Exception as exc:
            self.persistence_error = str(exc)
            raise
        self.persistence_error = ""
        with _RUN_LOCK:
            _PENDING.pop(self.result_id, None)

    def merge(self, *, execution=None):
        from .translation_task_merge import merge_saved_results

        # Read the checksummed durable generation; unsaved in-memory values never reach live content.
        payload = load_recovery_results(self.store, self.record)
        return merge_saved_results(
            self.ctx,
            self.store,
            self.record,
            payload,
            policy=self.policy,
            execution=execution,
            evidence=self.evidence,
        )
