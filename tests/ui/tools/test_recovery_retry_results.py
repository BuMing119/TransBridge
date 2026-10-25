"""Retrying remaining work must not rehydrate stale decisions from startup."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.conftest import make_entry
from transbridge.ui.tools.ai_translator.task_entry_results import TaskEntryResults
from transbridge.ui.tools.ai_translator.task_result_flow import TaskResultFlow


@pytest.mark.parametrize("decision,applied", [("rejected", False), ("accepted", True)])
def test_retry_preserves_decisions_made_after_recovery(decision, applied):
    done, failed = make_entry("done", original="Done"), make_entry("failed", original="Failed")
    task = SimpleNamespace(key="source", translate_entries=(done, failed), polish_entries=())
    ledger = TaskEntryResults((task,), "continued-run")
    ledger.entries[done.identity] = replace(
        ledger.entries[done.identity], status="succeeded", text="完成", stage=2, decision=decision, applied=applied
    )
    ledger.entries[failed.identity] = replace(ledger.entries[failed.identity], status="failed")
    old_record = {
        "results": {
            "rows": [
                {
                    "entry_key": done.identity.to_dict(),
                    "source": "source",
                    "action": "translation",
                    "before": {"original": "Done", "translation": "", "stage": 0, "context": "", "revision": 0},
                    "text": "完成",
                    "stage": 2,
                    "reason": "",
                    "decision": "pending",
                    "attempt": 1,
                    "report_details": {},
                }
            ]
        }
    }
    starts = []
    remaining = (SimpleNamespace(key="source", translate_entries=(failed,), polish_entries=(), entries=(failed,)),)
    run = SimpleNamespace(
        consistency=None,
        request=SimpleNamespace(),
        entries=ledger,
        recovery=SimpleNamespace(record=old_record),
        cancelled=False,
        shutting_down=False,
        preparing=True,
        _attempt=1,
        _start=starts.append,
        _prepare_failed=lambda error: pytest.fail(error),
        result_preparation=SimpleNamespace(start=lambda operation, on_success, on_error: on_success(operation())),
    )
    TaskResultFlow(run).start(remaining)
    assert starts == [remaining]
    assert run.entries.entries[done.identity].decision == decision
    assert run.entries.entries[done.identity].applied == applied
