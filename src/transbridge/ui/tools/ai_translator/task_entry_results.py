"""Per-entry execution, user decisions and publication for one task's attempts."""

from __future__ import annotations

from copy import copy, deepcopy
from dataclasses import dataclass, replace

from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.identity import Provenance
from transbridge.application.io.mutation import ChangeSet, EntryPatch, MutationStatus
from transbridge.application.translation.postprocess import PostProcessCandidate, ReportSnapshot


@dataclass(frozen=True)
class TaskEntryResult:
    source: str
    action: str
    before: object
    status: str = "not_started"
    text: str = ""
    stage: int = 0
    reason: str = ""
    decision: str = "pending"
    applied: bool = False
    attempt: int = 0


class TaskEntryResults:
    """Accept only the current attempt's keys; never infer success from old text."""

    def __init__(self, tasks, run_id):
        self.run_id = run_id
        self.entries = {
            entry.identity: TaskEntryResult(
                task.key, action, deepcopy(entry), text=entry.translation, stage=entry.stage
            )
            for task in tasks
            for action, entries in (("translation", task.translate_entries), ("polish", task.polish_entries))
            for entry in entries
        }

    @property
    def failed_keys(self):
        return frozenset(key for key, row in self.entries.items() if row.status == "failed" and not row.applied)

    def freeze(self):
        frozen = copy(self)
        frozen.entries = self.entries.copy()
        return frozen

    @property
    def ready_keys(self):
        return frozenset(
            key
            for key, row in self.entries.items()
            if row.status == "succeeded" and row.decision != "rejected" and not row.applied
        )

    @property
    def applied_keys(self):
        return frozenset(key for key, row in self.entries.items() if row.applied)

    def ingest(self, outcomes, keys, *, cancelled=False):
        by_source = {item.task.key: item for item in outcomes}
        for key in keys:
            previous = self.entries[key]
            if previous.applied or previous.decision == "rejected":
                raise ValueError("不能覆盖已应用或未采纳的条目")
            outcome = by_source.get(previous.source)
            status, text, stage, reason = self._execution(previous, key, outcome, cancelled)
            if status in {"not_started", "cancelled"} and not cancelled:
                status, reason = "failed", reason or "条目未完成"
            if status == "succeeded" and not text.strip():
                status, reason = "failed", "未生成有效译文"
            self.entries[key] = replace(
                previous,
                status=status,
                text=text,
                stage=stage,
                reason=reason,
                decision="pending",
                attempt=previous.attempt + 1,
            )

    @staticmethod
    def _execution(row, key, outcome, cancelled):
        missing = "not_started" if cancelled else "failed"
        fallback = "未生成有效结果" if outcome is None else outcome.error or "未生成有效结果"
        if outcome is None:
            return missing, row.before.translation, row.before.stage, fallback
        if row.action == "translation":
            result = getattr(outcome.translation, "entry_outcomes", {}).get(key)
            if result is None:
                return missing, row.before.translation, row.before.stage, fallback
            return result.status, result.text, result.stage, result.reason
        result = outcome.polish.get(row.before.id)
        if result is None:
            return missing, row.before.translation, row.before.stage, fallback
        processing = getattr(result, "processing_status", "")
        passed = bool(getattr(result, "accepted", False)) and getattr(result, "confidence", 0) > 0
        retained = getattr(result, "candidate_translation", None)
        # Cancellation blocks publishing, not evidence already validated by the pipeline.
        completed_before_cancel = cancelled and processing == "completed" and bool(retained)
        passed = passed or completed_before_cancel
        status = "succeeded" if passed and processing == "completed" else "failed"
        if processing in {"cancelled", "not_started"}:
            status = processing
        text = getattr(result, "polished_translation", "") or row.before.translation
        if completed_before_cancel:
            text = retained
        target_stage = getattr(result, "target_stage", None)
        stage = row.before.stage if target_stage is None else target_stage
        return status, text, stage, getattr(result, "note", "")

    def decide(self, decisions):
        if not set(decisions).issubset(self.ready_keys):
            raise ValueError("只能确认通过检查的未应用条目")
        for key, accepted in decisions.items():
            self.entries[key] = replace(self.entries[key], decision="accepted" if accepted else "rejected")

    def confirm_without_preview(self):
        for key in self.ready_keys:
            self.entries[key] = replace(self.entries[key], decision="accepted")

    def publish_to_drafts(self, tasks):
        """Copy only checked, confirmed values; TaskSession owns the live commit."""
        collections = {task.key: task.collection for task in tasks}
        selected = set()
        grouped = {}
        for key in self.ready_keys:
            row = self.entries[key]
            if row.decision != "accepted":
                continue
            collection = collections[row.source]
            entry = collection.get(key)
            if entry is None:
                raise ValueError("任务草稿缺少待应用条目，请重新运行任务。")
            grouped.setdefault(row.source, []).append((entry, row))
            selected.add(key)
        context = RequestContext(
            owner_id="ai-task-draft",
            run_id=self.run_id,
            permissions=frozenset({"entry.translation.write", "entry.stage.write"}),
        )
        for source, rows in grouped.items():
            changes = ChangeSet(
                run_id=self.run_id,
                patches=tuple(
                    EntryPatch.create(entry.identity, translation=row.text, stage=row.stage) for entry, row in rows
                ),
                expected_revisions=tuple((entry.identity, entry.revision) for entry, _ in rows),
                provenance=Provenance(self.run_id, context.owner_id, "ai-task-draft"),
            )
            result = collections[source].apply(changes, context)
            if result.status is not MutationStatus.APPLIED:
                details = "; ".join(item.message for item in result.diagnostics)
                raise RuntimeError(f"应用任务草稿失败（{source}）：{details or result.status}")
        return selected

    def mark_applied(self, keys):
        for key in keys:
            self.entries[key] = replace(self.entries[key], applied=True)

    def snapshot(self, source, *, cancelled=False):
        candidates = []
        for key, row in self.entries.items():
            if row.source != source:
                continue
            status = row.status
            if row.applied:
                result_status = "applied"
            elif row.decision == "rejected":
                result_status = "rejected"
            elif status == "succeeded":
                result_status = "not_applied" if cancelled else "pending"
            else:
                result_status = status
            candidates.append(
                PostProcessCandidate(
                    run_id=self.run_id,
                    entry_key=key,
                    before_revision=row.before.revision,
                    original=row.before.original,
                    before_text=row.before.translation,
                    text=row.text,
                    stage=row.stage,
                    context=row.before.context or "",
                    accepted=status == "succeeded",
                    report_details=tuple(
                        {
                            "processing_status": "completed" if status == "succeeded" else status,
                            "result_status": result_status,
                            "decision": row.decision,
                            "applied": row.applied,
                            "attempt": row.attempt,
                            "note": row.reason,
                        }.items()
                    ),
                )
            )
        failed = sum(dict(item.report_details)["processing_status"] == "failed" for item in candidates)
        accepted = sum(item.accepted for item in candidates)
        outcome = (
            OperationOutcome.CANCELLED
            if cancelled
            else (
                OperationOutcome.PARTIAL
                if failed and accepted
                else OperationOutcome.FAILED
                if failed
                else OperationOutcome.COMPLETED
            )
        )
        return ReportSnapshot(
            schema="transbridge.postprocess-report.v1",
            run_id=self.run_id,
            outcome=outcome,
            input_count=len(candidates),
            accepted_count=accepted,
            candidates=tuple(candidates),
            stage_outcomes=(),
            diagnostics=(),
            failure_count=failed,
        )
