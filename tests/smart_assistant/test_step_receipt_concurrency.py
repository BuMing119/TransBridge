"""Deterministic notification races against real persisted request receipts."""

from contextlib import contextmanager
from threading import Event, Thread, current_thread
from uuid import uuid4

import pytest

from transbridge.application.assistant_requests.models import ItemKind, RequestError, RequestItem, UserRequest
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.tools.types import ToolResult


@pytest.fixture
def pending_gates(tmp_path, monkeypatch, request):
    services = build_persistence_v2_services(
        tmp_path / "storage", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "2026-10-07T00:00:00Z"
    )
    service = services.gui_session_commands.assistant_requests
    gates = []
    try:
        for name in ("A", "B"):
            assert services.gui_session_commands.create_and_activate(name, RequestContext("owner")).is_success
            context = RequestContext("owner", session_id=services.session_lifecycle.active.aggregate.ref.identity.value)
            user_request = UserRequest(
                name, context.session_id, name, (RequestItem("read", "inspect", ItemKind.EXECUTION),)
            )
            service.transact(context, lambda state, r=user_request: state.update(requests=[r.to_dict()]))
            service.scheduler.activate(context.session_id, name)
            selected = service.scheduler.select_next_turn(
                context.session_id, name, (user_request,), allowed_tools=("receipt_read",)
            )
            gate = RequestExecutionGate(service, context, selected.admission)
            gate.prepare_steps([{"tool": "receipt_read", "args": {}}] * getattr(request, "param", 1))
            handle = gate.begin_step("receipt_read", {})
            with monkeypatch.context() as patch:
                patch.setattr(service, "update_request", _unavailable_storage)
                with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
                    gate.finish_step(handle, ToolResult.ok())
            gates.append(gate)
        yield service, gates
    finally:
        services.close()


def _unavailable_storage(*args, **kwargs):
    raise OSError("receipt storage is unavailable")


def _assert_settled(service, gate):
    assert not service.step_receipts.has_pending(gate.context, gate.admission.request_id)
    request = gate.current_request()
    assert not request.unsettled
    assert request.dispatches[0].status == "completed"
    assert len(request.dispatches[0].step_receipts) == 1
    assert not request.dispatches[0].step_attempts  # Recovery never executes a business attempt.


@pytest.mark.parametrize("same_session", [False, True])
def test_notification_during_another_delivery_is_drained(pending_gates, monkeypatch, same_session):
    service, gates = pending_gates
    first, second = gates[0], gates[0] if same_session else gates[1]
    serialized = service.serialized
    update_request = service.update_request
    released_lock, finish_notification = Event(), Event()
    errors = []
    writes = []

    def save(*args, **kwargs):
        writes.append(args[0].session_id)
        if same_session and len(writes) == 1:
            raise OSError("first recovery attempt still unavailable")
        return update_request(*args, **kwargs)

    @contextmanager
    def pause_after_persist(context):
        try:
            with serialized(context):
                yield
        finally:
            if current_thread().name == "receipt-notify" and not released_lock.is_set():
                released_lock.set()
                assert finish_notification.wait(5)

    def notify_first():
        try:
            service.notify(first.context.session_id)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(service, "serialized", pause_after_persist)
    monkeypatch.setattr(service, "update_request", save)
    worker = Thread(target=notify_first, name="receipt-notify")
    worker.start()
    try:
        assert released_lock.wait(5)
        service.notify(second.context.session_id)
    finally:
        finish_notification.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert len(writes) == 2
    _assert_settled(service, first)
    _assert_settled(service, second)


def test_reentrant_notification_requests_one_more_pass(pending_gates, monkeypatch):
    service, gates = pending_gates
    gate = gates[0]
    update_request = service.update_request
    writes = []

    def save(*args, **kwargs):
        writes.append(args[0].session_id)
        if len(writes) == 1:
            # A synchronous subscriber can notify while service.serialized is held.
            service.notify(gate.context.session_id)
            raise OSError("storage recovers after this attempt")
        return update_request(*args, **kwargs)

    monkeypatch.setattr(service, "update_request", save)
    service.notify(gate.context.session_id)
    assert len(writes) == 2
    _assert_settled(service, gate)


def test_persistent_failure_retries_only_when_notified(pending_gates, monkeypatch):
    service, gates = pending_gates
    gate = gates[0]
    writes = []

    def fail(*args, **kwargs):
        writes.append(args[0].session_id)
        raise OSError("still unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(service, "update_request", fail)
        service.notify(gate.context.session_id)
        assert len(writes) == 1
        assert service.step_receipts.has_pending(gate.context, gate.admission.request_id)
        with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
            gate.begin_step("receipt_read", {})
        service.notify(gate.context.session_id)
        assert len(writes) == 2
    service.notify(gate.context.session_id)
    _assert_settled(service, gate)


@pytest.mark.parametrize("pending_gates", [2], indirect=True)
def test_receipt_added_after_snapshot_is_included_in_notified_rescan(pending_gates, monkeypatch):
    service, gates = pending_gates
    gate = gates[0]
    serialized = service.serialized
    snapshot_taken, proceed = Event(), Event()
    errors = []

    @contextmanager
    def pause_before_lock(context):
        if current_thread().name == "receipt-snapshot" and not snapshot_taken.is_set():
            snapshot_taken.set()
            assert proceed.wait(5)
        with serialized(context):
            yield

    def recover():
        try:
            service.notify(gate.context.session_id)
        except BaseException as error:
            errors.append(error)

    monkeypatch.setattr(service, "serialized", pause_before_lock)
    worker = Thread(target=recover, name="receipt-snapshot")
    worker.start()
    try:
        assert snapshot_taken.wait(5)
        with monkeypatch.context() as patch:
            patch.setattr(service, "update_request", _unavailable_storage)
            with pytest.raises(RequestError, match="ADMISSION_PERSIST_FAILED"):
                gate.interrupt_steps()
        service.notify(gate.context.session_id)
    finally:
        proceed.set()
        worker.join(5)
    assert not worker.is_alive() and not errors
    assert not service.step_receipts.has_pending(gate.context, gate.admission.request_id)
    request = gate.current_request()
    assert not request.unsettled
    assert request.dispatches[0].status == "failed"
    assert [(receipt.step_id, receipt.status) for receipt in request.dispatches[0].step_receipts] == [
        ("0", "completed"),
        ("1", "cancelled"),
    ]
    assert not request.dispatches[0].step_attempts


def test_serialization_failure_releases_delivery_ownership(pending_gates, monkeypatch):
    service, gates = pending_gates
    gate = gates[0]

    @contextmanager
    def closed_service(context):
        raise RequestError("ADMISSION_CLOSED", "request service is unavailable")
        yield

    with monkeypatch.context() as patch:
        patch.setattr(service, "serialized", closed_service)
        service.notify(gate.context.session_id)
    assert service.step_receipts.has_pending(gate.context, gate.admission.request_id)
    service.notify(gate.context.session_id)
    _assert_settled(service, gate)
