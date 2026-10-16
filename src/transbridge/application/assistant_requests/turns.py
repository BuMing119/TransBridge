"""Durable admission and failure evidence for an already selected model turn."""

from dataclasses import replace

from .journal import EventCause
from .models import RequestError
from .scheduler import ready_item_ids


def accept_turn(service, context, selection):
    admission = selection.admission

    def accept(current):
        if (
            current.revision != admission.request_revision
            or current.lease_epoch != admission.request_lease_epoch
            or not set(admission.ready_item_ids) <= set(ready_item_ids(current))
        ):
            raise RequestError("TURN_LEASE_STALE", "请求在接纳前发生了变化，请重新继续。")
        return replace(current, automatic_turns=max(current.automatic_turns, selection.request.automatic_turns))

    try:
        return service.update_request(
            context,
            selection.request.request_id,
            accept,
            cause=EventCause(
                "turn.selected",
                references={
                    "turn_id": admission.turn_id,
                    "request_ids": [admission.request_id],
                    "item_ids": list(admission.ready_item_ids),
                },
                record_unchanged=True,
            ),
        )
    except Exception:
        service.scheduler.release(admission)
        raise


def record_turn_failure(service, context, admission, *, batch=None, history=()):
    if admission.request_id:
        service.command(
            context,
            admission.request_id,
            "pause",
            admission.request_revision,
            reason="user_paused",
            cause=EventCause("turn.failed", references={"turn_id": admission.turn_id}),
        )
    elif batch is not None:
        service.transact(
            context,
            lambda state: None,
            history=history,
            cause=EventCause(
                "routing.failed",
                references={
                    "turn_id": admission.turn_id,
                    "batch_id": batch.batch_id,
                    "message_ids": [source.message_id for source in batch.sources],
                },
                details={"code": "MODEL_TURN_FAILED"},
                record_unchanged=True,
            ),
        )


def reject_routing_input(service, context, admission, batch, *, code):
    """Retain rejected input evidence without blocking later, corrected submissions."""
    message_ids = {source.message_id for source in batch.sources}

    def reject(state):
        entry = next(
            (value for value in state.get("batches", ()) if value["batch"]["batch_id"] == batch.batch_id), None
        )
        if entry is None or entry.get("status") != "routing":
            return
        entry.update(status="rejected", rejection_code=code)
        for item in state.get("ingress", ()):
            if item["message_id"] in message_ids and item.get("batch_id") == batch.batch_id:
                item.update(status="rejected", rejection_code=code)

    with service.serialized(context), service.scheduler.serialized(admission):
        if admission.stage != "routing" or admission.session_id != context.session_id:
            raise RequestError("TURN_LEASE_STALE", "input rejection belongs to another routing turn")
        service.transact(
            context,
            reject,
            cause=EventCause(
                "routing.rejected",
                references={
                    "turn_id": admission.turn_id,
                    "batch_id": batch.batch_id,
                    "message_ids": sorted(message_ids),
                },
                details={"code": code},
            ),
        )
