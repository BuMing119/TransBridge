from dataclasses import replace
import gc
from threading import Event
import weakref

import pytest

from tests.application.terminology.test_build import _entry, _snapshot, _source
from tests.contracts.terminology.test_runtime import _Clock, _CommitPort, _Ids, _owner, _request
from transbridge.application.io import ParseResult
from transbridge.application.tasks import BoundedThreadPoolBackend, JobState, TaskRuntime
from transbridge.application.terminology.runtime import (
    TerminologyTaskEntrypoint,
    TerminologyWorkloadExecution,
    TerminologyWorkloadRegistry,
)
from transbridge.application.terminology.workloads import (
    BuildWorkloadRequest,
    TerminologyWorkloadResult,
    TerminologyWorkloadType,
    terminology_job_spec,
)


class _PayloadMarker:
    pass


def _payload():
    marker = _PayloadMarker()
    source = _source("source-a")
    snapshot = replace(source.lease.snapshot, metadata=(("marker", marker), ("nested", b"localized bytes")))
    parsed = ParseResult.completed(
        source.registration.format_id,
        snapshot.source,
        snapshot,
        (_entry("source-a", "one", "Dragon", "translation"),),
    )
    source = replace(source, lease=replace(source.lease, snapshot=snapshot, saved_result=parsed))
    return _snapshot((source,)), weakref.ref(marker)


@pytest.mark.parametrize("outcome", ["completed", "cancelled_running", "cancelled_queued", "failed"])
def test_build_payload_is_ephemeral_on_the_production_backend(outcome):
    backend = BoundedThreadPoolBackend(max_workers=1)
    runtime = TaskRuntime(id_generator=_Ids(), clock=_Clock(), backend=backend)
    registry = TerminologyWorkloadRegistry()
    release = Event()
    started = Event()
    observed = []

    if outcome == "cancelled_queued":

        def block():
            started.set()
            assert release.wait(5)

        backend.start("blocker", block)
        assert started.wait(5)

    def runner(request, context):
        source = request.input_snapshot.sources[0]
        assert source.lease.saved_result is not None
        assert source.lease.snapshot.content is not None
        assert dict(source.lease.snapshot.metadata)["nested"] == b"localized bytes"
        observed.append(True)
        if outcome == "failed":
            raise RuntimeError("build failed")
        if outcome == "cancelled_running":
            runtime.cancel(context.lease.ref, context.lease.owner)
        return TerminologyWorkloadExecution(TerminologyWorkloadResult(request.workload_type), lambda: None)

    registry.bind(TerminologyWorkloadType.BUILD, runner)
    tasks = TerminologyTaskEntrypoint(runtime, registry, _CommitPort())
    owner = replace(_owner(), variant_id="main")

    def enqueue():
        snapshot, marker = _payload()
        request = BuildWorkloadRequest(
            project_id=snapshot.project_id,
            variant_id=snapshot.variant_id,
            expected=_request().expected,
            build_key="build-key",
            input_snapshot=snapshot,
        )
        assert terminology_job_spec(request) == terminology_job_spec(replace(request, input_snapshot=None))
        ref = tasks.submit(request, owner).ref
        assert tasks.request(ref, owner).input_snapshot is None
        return ref, marker

    try:
        ref, marker = enqueue()
        if outcome == "cancelled_queued":
            tasks.cancel(ref, owner)
        release.set()
        assert backend.close(timeout=5)
        expected = {
            "completed": JobState.COMPLETED,
            "cancelled_running": JobState.CANCELLED,
            "cancelled_queued": JobState.CANCELLED,
            "failed": JobState.FAILED,
        }[outcome]
        assert runtime.get(ref, owner).state is expected
        assert observed == ([] if outcome == "cancelled_queued" else [True])
        gc.collect()
        assert marker() is None
        assert tasks.request(ref, owner).input_snapshot is None
    finally:
        release.set()
        runtime.close()
