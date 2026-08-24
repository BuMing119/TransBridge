from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import threading
import time

from transbridge.application.contracts import Diagnostic, OperationCounts, OperationResult
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime, ThreadBackend
from transbridge.ui.operations import (
    OperationKind,
    OperationPreflightResult,
    OperationTaskAdapter,
    OperationTaskRequest,
)


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"operation-run-{self.value}"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self):
        self.value += timedelta(microseconds=1)
        return self.value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def test_partial_result_retry_uses_failed_subset_fresh_preflight_and_new_run() -> None:
    runtime = TaskRuntime(
        id_generator=Ids(),
        clock=Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )
    owner = OwnerRef("gui", "workbench", project_id="p")
    adapter = OperationTaskAdapter(runtime)
    refreshed_digest = digest("fresh")

    def successful(_context):
        return OperationResult.completed(
            {"outcomes": ({"object_ref": "b", "status": "succeeded"},)},
            counts=OperationCounts(succeeded=1),
        )

    def retry_factory(refs):
        assert refs == ("b",)
        return OperationTaskRequest(
            OperationKind.UPLOAD,
            refreshed_digest,
            "remote:b",
            "重试上传失败项",
            successful,
            False,
            refs,
        )

    def partial(_context):
        return OperationResult.partial(
            {
                "outcomes": (
                    {"object_ref": "a", "status": "succeeded"},
                    {"object_ref": "b", "status": "failed", "retryable": True, "code": "TIMEOUT"},
                )
            },
            counts=OperationCounts(succeeded=1, failed=1),
            diagnostics=(Diagnostic("REMOTE_TIMEOUT", "one object failed", retryable=True),),
        )

    first = adapter.submit(
        OperationTaskRequest(
            OperationKind.UPLOAD,
            digest("first"),
            "remote:a,b",
            "上传",
            partial,
            False,
            ("a", "b"),
            retry_factory,
        ),
        owner,
    )
    second = adapter.retry_failed(
        first,
        owner,
        re_preflight=lambda _request, _refs: OperationPreflightResult(
            OperationKind.UPLOAD,
            refreshed_digest,
            "remote-r2",
            (),
            ("upload b with a new idempotency key",),
        ),
    )

    assert second.run_id != first.run_id
    assert runtime.get(first, owner).state is JobState.COMPLETED
    assert runtime.get(second, owner).state is JobState.COMPLETED


def test_cancel_invalidates_late_commit_permit_and_never_mutates() -> None:
    runtime = TaskRuntime(id_generator=Ids(), clock=Clock(), backend=ThreadBackend())
    owner = OwnerRef("gui", "workbench", project_id="p")
    adapter = OperationTaskAdapter(runtime)
    ready = threading.Event()
    release = threading.Event()
    mutated: list[str] = []

    def workload(context):
        guard = context.publish_commit_guard()
        ready.set()
        assert release.wait(2)
        decision = guard.commit(context.ref.run_id, lambda: mutated.append("formal"))
        assert not decision.accepted
        return OperationResult.cancelled(run_id=context.ref.run_id)

    ref = adapter.submit(
        OperationTaskRequest(OperationKind.WRITE, digest("late"), "file:x", "写回", workload, True),
        owner,
    )
    assert ready.wait(2)
    runtime.cancel(ref, owner)
    release.set()
    deadline = time.monotonic() + 2
    while runtime.get(ref, owner).state is not JobState.CANCELLED and time.monotonic() < deadline:
        time.sleep(0.01)

    assert runtime.get(ref, owner).state is JobState.CANCELLED
    assert mutated == []


def test_100_short_operation_lifecycles_are_bounded() -> None:
    runtime = TaskRuntime(
        id_generator=Ids(),
        clock=Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )
    owner = OwnerRef("gui", "workbench")
    adapter = OperationTaskAdapter(runtime, max_results=16)

    def complete(_context):
        return OperationResult.completed(counts=OperationCounts(succeeded=1))

    started = time.perf_counter()
    refs = tuple(
        adapter.submit(
            OperationTaskRequest(
                OperationKind.DOWNLOAD,
                digest(f"lifecycle-{index}"),
                f"remote:{index}",
                "下载",
                complete,
                False,
            ),
            owner,
        )
        for index in range(100)
    )

    assert time.perf_counter() - started < 1.0
    assert all(runtime.get(ref, owner).state is JobState.COMPLETED for ref in refs)
