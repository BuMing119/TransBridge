from __future__ import annotations

from datetime import UTC, datetime, timedelta

from transbridge.application.tasks import JobState, OwnerRef, TaskRuntime
from transbridge.application.terminology.workloads import (
    BuildWorkloadRequest,
    TerminologyExpectedState,
    terminology_job_spec,
)
from transbridge.ui.tools.terminology.task_adapter import TerminologyTaskAdapter


class _Ids:
    def new_id(self) -> str:
        return "terminology-ui-run"


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 28, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


def test_adapter_projects_progress_and_immediate_cancelling_state_without_qthread():
    runtime = TaskRuntime(id_generator=_Ids(), clock=_Clock())
    owner = OwnerRef("operator", "gui", project_id="project-1", variant_id="variant-1")
    request = BuildWorkloadRequest(
        project_id="project-1",
        variant_id="variant-1",
        expected=TerminologyExpectedState(0, 0, "graph", "sources"),
        build_key="build-key",
    )
    changes = []
    adapter = TerminologyTaskAdapter(runtime, owner, changes.append)
    adapter.start()
    ref = runtime.submit(terminology_job_spec(request), owner).ref
    runtime.start(ref, owner)
    runtime.update_progress(
        ref,
        owner,
        {
            "phase": "extract",
            "completed": 2,
            "total": 5,
            "current_object": "plugin.esm",
            "llm_submitted": 3,
            "llm_completed": 2,
            "llm_waiting": 1,
            "llm_retries": 1,
            "llm_elapsed_ms": 1200,
        },
    )

    assert adapter.cancel(ref)
    latest = adapter.states()[-1]
    assert latest.state is JobState.CANCELLING
    assert latest.message == "正在停止"
    assert (latest.completed, latest.total, latest.current_object) == (2, 5, "plugin.esm")
    assert (latest.llm_submitted, latest.llm_completed, latest.llm_waiting) == (3, 2, 1)
    assert (latest.llm_retries, latest.llm_elapsed_ms) == (1, 1200)

    adapter.close()
    assert adapter.closed
