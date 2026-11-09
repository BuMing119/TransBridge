"""Retain downloaded translation candidates before any project change."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from transbridge.application.tasks.result_application import ResultApplication, select_saved_rows


class DownloadTaskResults:
    def __init__(self, store, request, commands, run_id):
        self.store = store.recovery(owner_id=request.owner_id, session_id=request.session_id)
        self.service = ResultApplication.for_commands(self.store, commands)
        self.request, self.run_id = request, run_id
        self._created = False

    def retain(self, candidates, review, *, state):
        self.store.acquire(self.run_id)
        rows = [_candidate(change) for change in candidates]
        fields = {
            "state": state,
            "candidate_rows": rows,
            "unmapped": [
                {
                    "entry_key": row.local.entry_key.to_dict(),
                    "original": item.entry.original,
                    "translation": item.entry.translation,
                    "stage": item.entry.stage,
                    "context": row.local.context,
                }
                for row in review
                if not row.resolved
                for item in row.candidates
            ],
        }
        if self._created:
            self.store.update_metadata(self.run_id, **fields)
        else:
            self.store.create({
                "task_id": self.run_id,
                "project_id": self.request.project_id,
                "variant_id": self.request.variant_id,
                "mode": "download",
                "config_digest": "paratranz-download",
                "result_kind": "download",
                "origin": "smart-assistant",
                "results_only": True,
                "supported": False,
                "created_at": datetime.now(UTC).isoformat(),
                "sources": [],
                **fields,
            })
            self._created = True

    def prepare(self, candidates, policy, per_entry_policies):
        rows = select_saved_rows(self.store, self.run_id, candidates)
        decision = repr(tuple((key.serialize(), str(value)) for key, value in sorted(per_entry_policies.items())))
        return self.service.prepare(
            self.run_id, self.request.project_id, self.request.variant_id, rows, policy, decision=decision
        )

    def finish(self, application, plan):
        outcome = self.service.finish(application, plan) if application is not None else None
        self._finish(
            outcome,
            applied=len(plan.applied),
            unchanged=len(plan.unchanged_keys),
            pending=len(plan.pending),
        )

    def finish_reconciled(self, application):
        receipt = application.receipt
        if receipt is None:
            return
        counts = Counter(item["state"] for item in receipt.get("dispositions", ()))
        self._finish(
            application,
            applied=counts["applied"],
            unchanged=counts["unchanged"] if counts else len(receipt["entries"]),
            pending=counts["conflicted"],
        )

    def _finish(self, application, *, applied, unchanged, pending):
        self.store.update_metadata(
            self.run_id,
            state="completed",
            merge={
                "applied_count": applied,
                "unchanged_count": unchanged,
                "pending_count": pending,
                "application_state": None if application is None else application.state.value,
            },
        )

    def close(self):
        self.store.release()


def _candidate(change):
    base, incoming = change.base, change.incoming
    return {
        "entry_key": base.entry_key.to_dict(),
        "source": base.entry_key.namespace.value,
        "action": "translation",
        "before": {
            "original": base.original,
            "translation": base.translation,
            "stage": base.stage,
            "context": base.context or "",
            "source_locator": base.source_locator,
        },
        "text": incoming.translation,
        "stage": incoming.stage,
        "decision": "pending",
    }
