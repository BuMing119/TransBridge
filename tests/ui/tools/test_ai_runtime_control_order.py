"""Real AI controls retain authority order across concurrent runtime delivery."""

from datetime import UTC, datetime
from threading import Event, Thread, current_thread
from types import SimpleNamespace

from PyQt6.QtCore import Qt
import pytest

from transbridge.application.tasks import JobState, TaskRuntime
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.task_worker import AiTaskWorker


@pytest.fixture
def run():
    runtime = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: "ordered-ai-controls"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
    )
    controller = RunController(task_runtime=runtime)
    request = controller.begin("translate", LLMConfig(), [])
    value = SimpleNamespace(runtime=runtime, controller=controller, request=request)
    try:
        yield value
    finally:
        if controller._active.activity is not None:
            controller._active.activity.close()
        runtime.close()


def _thread(action, name):
    errors = []

    def execute():
        try:
            action()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=execute, name=name, daemon=True)
    thread.errors = errors
    thread.start()
    return thread


def _joined(*threads):
    for thread in threads:
        thread.join(3)
        assert not thread.is_alive(), f"control deadlocked: {thread.name}"
        assert not thread.errors


def test_older_resume_event_cannot_override_newer_pause(run):
    entered, release = Event(), Event()

    def delay_resume(event):
        if event.previous_state is JobState.PAUSED and event.snapshot.state is JobState.RUNNING:
            entered.set()
            assert release.wait(3)

    subscription = run.runtime.subscribe(delay_resume)
    adapter = run.controller.create_activity(run.request)
    worker = AiTaskWorker(run.request, ())
    adapter.bind_worker(worker)
    ref, owner = run.request.runtime_ref, run.request.spec.owner
    run.runtime.pause(ref, owner)
    resume = _thread(lambda: run.runtime.resume(ref, owner), "resume")
    try:
        assert entered.wait(3)
        run.runtime.pause(ref, owner)
        assert worker.is_paused
    finally:
        release.set()
        _joined(resume)
        subscription.close()
    assert run.runtime.get(ref, owner).state is JobState.PAUSED
    assert worker.is_paused


@pytest.mark.parametrize("origin", ["event", "bind"])
def test_state_acceptance_and_worker_action_are_one_ordered_delivery(run, origin):
    entered, release, paused = Event(), Event(), Event()
    subscription = run.runtime.subscribe(
        lambda event: paused.set() if event.snapshot.state is JobState.PAUSED else None
    )
    adapter = run.controller.create_activity(run.request)

    class Worker(AiTaskWorker):
        def resume(self):
            if current_thread().name == "delayed-action":
                entered.set()
                assert release.wait(3)
            super().resume()

    worker = Worker(run.request, ())
    ref, owner = run.request.runtime_ref, run.request.spec.owner
    if origin == "event":
        adapter.bind_worker(worker)
        run.runtime.pause(ref, owner)
        paused.clear()

    def action():
        if origin == "event":
            run.runtime.resume(ref, owner)
        else:
            adapter.bind_worker(worker)

    old = _thread(action, "delayed-action")
    newer = None
    try:
        assert entered.wait(3)
        newer = _thread(lambda: run.runtime.pause(ref, owner), "newer-pause")
        assert paused.wait(3)
    finally:
        release.set()
        _joined(old, *(() if newer is None else (newer,)))
        subscription.close()
    assert run.runtime.get(ref, owner).state is JobState.PAUSED
    assert worker.is_paused


@pytest.mark.parametrize("detach", ["unbind", "close"])
def test_detach_waits_for_inflight_action_and_fences_old_worker(run, detach):
    entered, release, detached = Event(), Event(), Event()
    adapter = run.controller.create_activity(run.request)

    class Worker(AiTaskWorker):
        def resume(self):
            entered.set()
            assert release.wait(3)
            super().resume()

    worker = Worker(run.request, ())
    binding = _thread(lambda: adapter.bind_worker(worker), "binding")
    detaching = None

    def detach_worker():
        adapter.unbind_worker(worker) if detach == "unbind" else adapter.close()
        detached.set()

    try:
        assert entered.wait(3)
        detaching = _thread(detach_worker, "detaching")
        assert not detached.wait(0.1), "detach returned with a worker action still in flight"
    finally:
        release.set()
        _joined(binding, *(() if detaching is None else (detaching,)))
    run.runtime.pause(run.request.runtime_ref, run.request.spec.owner)
    assert not worker.is_paused, "detached worker received a later runtime control"


def test_bind_uses_newer_delivered_authority_when_its_snapshot_is_delayed(run, monkeypatch):
    snapshot_read, release, pause_delivered = Event(), Event(), Event()
    adapter = run.controller.create_activity(run.request)
    subscription = run.runtime.subscribe(
        lambda event: pause_delivered.set() if event.snapshot.state is JobState.PAUSED else None
    )
    worker = AiTaskWorker(run.request, ())
    get = run.runtime.get

    def delayed_get(*args):
        snapshot = get(*args)
        if current_thread().name == "binding":
            snapshot_read.set()
            assert release.wait(3)
        return snapshot

    monkeypatch.setattr(run.runtime, "get", delayed_get)
    binding = _thread(lambda: adapter.bind_worker(worker), "binding")
    pausing = None
    try:
        assert snapshot_read.wait(3)
        pausing = _thread(lambda: run.runtime.pause(run.request.runtime_ref, run.request.spec.owner), "pausing")
        assert pause_delivered.wait(3), "binding held an adapter lock across the runtime snapshot read"
    finally:
        release.set()
        _joined(binding, *(() if pausing is None else (pausing,)))
        subscription.close()
    assert worker.is_paused


def test_old_worker_unbind_does_not_detach_replacement(run):
    adapter = run.controller.create_activity(run.request)
    old, current = AiTaskWorker(run.request, ()), AiTaskWorker(run.request, ())
    adapter.bind_worker(old)
    adapter.bind_worker(current)
    adapter.unbind_worker(old)
    run.runtime.pause(run.request.runtime_ref, run.request.spec.owner)
    assert current.is_paused and not old.is_paused


def test_synchronous_worker_signal_defers_nested_control_until_method_returns(run):
    adapter = run.controller.create_activity(run.request)
    ref, owner = run.request.runtime_ref, run.request.spec.owner
    reentered = []

    class Worker(AiTaskWorker):
        resuming = False

        def resume(self):
            self.resuming = True
            try:
                super().resume()
            finally:
                self.resuming = False

        def pause(self):
            if self.resuming:
                # Fail explicitly instead of hanging on the actual worker's
                # non-reentrant control lock held by resume's signal emission.
                reentered.append(True)
                raise RuntimeError("worker control recursively reentered")
            super().pause()

    worker = Worker(run.request, ())
    adapter.bind_worker(worker)
    run.runtime.pause(ref, owner)
    worker.pause_state_changed.connect(
        lambda state: run.runtime.pause(ref, owner) if state == "running" else None,
        Qt.ConnectionType.DirectConnection,
    )
    assert adapter.resume()
    assert run.runtime.get(ref, owner).state is JobState.PAUSED
    assert worker.is_paused
    assert not reentered


def test_worker_callback_and_concurrent_adapter_cancel_have_no_lock_cycle(run):
    entered, cancelled = Event(), Event()
    subscription = run.runtime.subscribe(
        lambda event: cancelled.set() if event.snapshot.state is JobState.CANCELLING else None
    )
    adapter = run.controller.create_activity(run.request)

    class Worker(AiTaskWorker):
        def resume(self):
            if current_thread().name == "binding":
                entered.set()
                assert cancelled.wait(3)
                assert adapter.progress(0, 1, "cancellation observed")
            super().resume()

    worker = Worker(run.request, ())
    binding = _thread(lambda: adapter.bind_worker(worker), "binding")
    cancelling = None
    try:
        assert entered.wait(3)
        cancelling = _thread(adapter.request_cancel, "cancelling")
        _joined(binding, cancelling)
    finally:
        subscription.close()
    assert worker.was_cancelled
    assert run.runtime.get(run.request.runtime_ref, run.request.spec.owner).state is JobState.CANCELLING
