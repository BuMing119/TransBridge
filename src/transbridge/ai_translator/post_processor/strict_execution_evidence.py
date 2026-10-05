"""Track completed strict-stage work before accepting a final decision."""

from __future__ import annotations

from dataclasses import replace


class StrictExecutionEvidence:
    def __init__(self, entries):
        self.keys = {str(entry.id) for entry in entries}
        self.required = {}
        self.completed = {}
        self.failures = {}

    def require(self, phase, entries):
        self.required.setdefault(phase, set()).update(str(entry.id) for entry in entries)

    def complete(self, phase, entries):
        self.completed.setdefault(phase, set()).update(str(entry.id) for entry in entries)

    def accept_results(self, phase, values, text_field=None):
        completed = self.completed.setdefault(phase, set())
        for key, value in values.items():
            if getattr(value, "confidence", 0) <= 0 or (text_field and not getattr(value, text_field, "").strip()):
                self.failures[str(key)] = getattr(value, "note", "") or f"{phase} 未返回有效结果"
            else:
                completed.add(str(key))

    def fail(self, phase, entries, error):
        for entry in entries:
            self.failures[str(entry.id)] = f"{phase} 失败：{error}"

    def check_issues(self, issues):
        for issue in issues:
            if issue.execution_failed:
                self.failures[str(issue.entry_id)] = issue.message

    def incomplete(self):
        failures = dict(self.failures)
        for phase, required in self.required.items():
            for key in required - self.completed.get(phase, set()):
                failures.setdefault(key, f"{phase} 未完成")
        return failures

    def guard_decisions(self, decisions):
        """Do not let a fallback rule approve a missing/failed upstream stage."""
        from .llm_arbiter import ArbiterDecision

        guarded = dict(decisions)
        for key, reason in self.incomplete().items():
            decision = guarded.get(key)
            guarded[key] = (
                replace(
                    decision,
                    verdict="reject" if decision.verdict == "reject" else "pending",
                    confidence=0.0,
                    reason=reason,
                )
                if decision is not None
                else ArbiterDecision(key, "pending", reason, 0.0, "重试失败条目")
            )
        return guarded

    def publish(self, result, *, cancelled, finished):
        failures = self.incomplete()
        decisions = result.decisions or {}
        for key in self.keys:
            decision = decisions.get(key)
            passed = decision is not None and decision.verdict == "pass" and decision.confidence > 0
            if key in failures:
                status = "cancelled" if cancelled and key not in self.failures else "failed"
            elif finished and passed:
                status = "completed"
            else:
                status = "cancelled" if cancelled else "failed"
            result.processing_statuses[key] = status
            if status != "completed":
                result.processing_notes[key] = failures.get(key) or (
                    "任务已取消" if cancelled else getattr(decision, "reason", "") or "后处理未完成"
                )
