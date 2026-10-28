"""Retain non-model candidates in the shared task store and merge command."""

from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from transbridge.application.translation.result_merge import ResultConflictPolicy, source_locator_signature
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.config.task_results import TaskResultPreferences

from .base import ToolResult
from .translation_task_results import _LOCATIONS, _RUN_LOCK, _authorized, task_ports

_PENDING = {}
_LOCK = RLock()


class EntryResultStore(TaskRecoveryStore):
    """Use the same atomic record for raw candidates, including explicit empty text."""

    def load_results(self, task_id):
        return {"rows": self.load(task_id).get("candidate_rows", [])}

    def save_results(self, task_id, rows):
        self.update_metadata(task_id, candidate_rows=rows)


class EntryTaskResults:
    def __init__(self, ctx, collection, *, kind, policy_section="ai_policy", inputs=None):
        self.ctx = ctx
        self.app, self.commands, self.context, self.identity, root = task_ports(ctx)
        self.store = EntryResultStore(root)
        self.result_id = uuid4().hex
        self.base = {entry.identity: deepcopy(entry) for entry in collection or ()}
        provider = getattr(self.app, "task_result_preferences_provider", None) or TaskResultPreferences
        self.policy = ResultConflictPolicy(getattr(provider(), policy_section))
        self.source = str(getattr(self.app, "active_key", None) or "assistant")
        self.record = {
            "task_id": self.result_id,
            "origin": "smart-assistant",
            "owner_id": self.context.owner_id,
            "session_id": self.context.session_id,
            "project_id": self.identity[0],
            "variant_id": self.identity[1],
            "mode": kind,
            "result_kind": kind,
            "results_only": True,
            "supported": False,
            "recovery_error": "使用 get_task_result 查询、merge_task_result 合入；不重新计算或请求远端。",
            "created_at": datetime.now(UTC).isoformat(),
            "config_digest": "fixed-entry-inputs",
            "state": "running",
            "sources": [],
            "conflict_policy": self.policy.value,
            "fixed_inputs": inputs or {},
            "baseline_entries": [entry.to_dict() for entry in self.base.values()],
            "source_key": self.source,
            "source_fingerprint": getattr(ctx, "_target_source_fingerprint", None)
            or getattr(getattr(getattr(self.app, "active_slot", None), "source_snapshot", None), "sha256", ""),
            "candidate_rows": [],
            "entries": [],
            "unmapped": [],
        }
        self.prepared = False
        self.persistence_error = ""
        with _RUN_LOCK:
            _LOCATIONS[self.result_id] = (root, self.context.owner_id, self.context.session_id)
        with _LOCK:
            _PENDING[self.result_id] = self

    def candidates(self, entries, *, remote=False):
        """Only translation/stage may enter the common merge; retain every raw row."""
        rows, unmapped = [], deepcopy(self.record.get("parse_unmapped", []))
        entries = tuple(entries)
        self.record["entries"] = deepcopy(list(entries)) if remote else [entry.to_dict() for entry in entries]
        local_matches, remote_matches = defaultdict(list), defaultdict(list)
        if remote:
            for entry in self.base.values():
                local_matches[entry.key, entry.original].append(entry)
            for raw in entries:
                remote_matches[raw.get("key"), raw.get("original", "")].append(raw)
        used = set()
        for raw in entries:
            if remote:
                match_key = raw.get("key"), raw.get("original", "")
                matches = local_matches[match_key]
                entry = matches[0] if len(matches) == len(remote_matches[match_key]) == 1 else None
                text, stage = raw.get("translation", ""), raw.get("stage", 0)
            else:
                entry = self.base.get(raw.identity)
                text, stage = raw.translation, raw.stage
                if entry is not None and (entry.original, entry.context, entry.metadata, entry.string_id) != (
                    raw.original,
                    raw.context,
                    raw.metadata,
                    raw.string_id,
                ):
                    entry = None
            if entry is None or entry.identity in used:
                unmapped.append(raw if remote else raw.to_dict())
                continue
            used.add(entry.identity)
            rows.append({
                "entry_key": entry.identity.to_dict(),
                "source": self.source,
                "action": "translation",
                "before": {
                    "original": entry.original,
                    "translation": entry.translation,
                    "stage": entry.stage,
                    "context": entry.context or "",
                    "revision": entry.revision.value,
                    "source_locator": source_locator_signature(
                        entry.metadata, entry.string_id, self.record["source_fingerprint"]
                    ),
                },
                "text": text,
                "stage": stage,
                "decision": "pending",
            })
        self.record.update(candidate_rows=rows, unmapped=unmapped)

    def persist(self, *, state="completed", error=""):
        self.record.update(state=state, error=error)
        self.retry_save()

    def retry_save(self):
        with _LOCK:
            _PENDING[self.result_id] = self
        try:
            if self.prepared:
                metadata = self.store.update_metadata(
                    self.result_id, **{k: v for k, v in self.record.items() if k != "task_id"}
                )
                self.record.update(metadata)
            else:
                self.record = self.store.create(self.record)
                self.prepared = True
        except Exception as exc:
            self.persistence_error = str(exc)
            raise
        self.persistence_error = ""
        with _LOCK:
            _PENDING.pop(self.result_id, None)

    def finish(self, *, auto_merge=True, error=""):
        try:
            parse_partial = self.record.get("parse_outcome") == "partial"
            self.persist(state="partial" if error or parse_partial else "completed", error=error)
            summary, warning = ({}, "")
            if auto_merge:
                from .translation_task_merge import merge_saved_results

                summary, warning = merge_saved_results(
                    self.ctx, self.store, self.record, self.store.load_results(self.result_id), policy=self.policy
                )
            return ToolResult(
                success=not error and (not auto_merge or not self.record["unmapped"]),
                partial=bool(error or parse_partial or self.record["unmapped"] or summary.get("pending_count")),
                message="成果已保存" + (f"；{error}" if error else ""),
                data={
                    "result_id": self.result_id,
                    "entries": self.record["entries"],
                    "merge": summary,
                    "unmapped_count": len(self.record["unmapped"]),
                    "parse_outcome": self.record.get("parse_outcome"),
                    "parse_diagnostics": self.record.get("parse_diagnostics", []),
                },
                warnings=[warning] if warning else None,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                partial=True,
                message=f"成果已保留，保存或合入待重试：{exc}",
                data={"result_id": self.result_id, "entries": self.record["entries"]},
                recovery_action="使用 get_task_result 查询，merge_task_result 重试；无需重新计算。",
            )


def pending_result(ctx, result_id):
    from .translation_task_results import _caller

    with _LOCK:
        task = _PENDING.get(result_id)
    if task is None:
        return None
    _app, caller = _caller(ctx)
    _authorized(task.record, caller)
    return task


def pending_results(ctx):
    from .translation_task_results import _caller

    _app, caller = _caller(ctx)
    with _LOCK:
        pending = tuple(_PENDING.values())
    return [
        task
        for task in pending
        if (task.context.owner_id, task.context.session_id) == (caller.owner_id, caller.session_id)
    ]
