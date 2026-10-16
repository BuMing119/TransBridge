from dataclasses import replace

import pytest

from tests.contracts.terminology.test_runtime import _CommitPort, _owner, _request, _runtime
from transbridge.application.tasks import JobState, TaskCancelled
from transbridge.application.terminology.runtime import TerminologyTaskEntrypoint, TerminologyWorkloadRegistry
from transbridge.application.terminology.workloads import TerminologyWorkloadType


def test_task_failure_retains_cause_and_checks_owner_before_returning_it():
    runtime = _runtime()
    registry = TerminologyWorkloadRegistry()

    def fail(request, context):
        raise ValueError("术语草稿尚有待复核项：Dragon")

    registry.bind(TerminologyWorkloadType.BUILD, fail)
    tasks = TerminologyTaskEntrypoint(runtime, registry, _CommitPort())
    submitted = tasks.submit(_request(), _owner()).ref

    assert runtime.get(submitted, _owner()).state is JobState.FAILED
    assert tasks.failure(submitted.run_id, _owner()) == "术语草稿尚有待复核项：Dragon"
    assert tasks.result_for_run(submitted.run_id, _owner()) is None
    with pytest.raises(PermissionError):
        tasks.failure(submitted.run_id, replace(_owner(), owner_id="someone-else"))
    with pytest.raises(PermissionError):
        tasks.result_for_run(submitted.run_id, replace(_owner(), variant_id="other"))


def test_cancelled_task_has_no_failure_message():
    runtime = _runtime()
    registry = TerminologyWorkloadRegistry()

    def cancel(request, context):
        raise TaskCancelled("cancelled")

    registry.bind(TerminologyWorkloadType.BUILD, cancel)
    tasks = TerminologyTaskEntrypoint(runtime, registry, _CommitPort())
    submitted = tasks.submit(_request(), _owner()).ref

    assert runtime.get(submitted, _owner()).state is JobState.CANCELLED
    assert tasks.failure(submitted.run_id, _owner()) is None
