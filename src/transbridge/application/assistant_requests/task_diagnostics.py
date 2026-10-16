"""Durable task diagnostics are audit evidence, never proof of a committed write."""

from __future__ import annotations

import json

from transbridge.application.security import SecretRedactor

from .models import Evidence, RequestError, digest
from .reducer import add_evidence


def append_task_diagnostic(request, snapshot, effect_id: str, message: str):
    if snapshot.owner.session_id != request.session_id:
        raise RequestError("REQUEST_SCOPE_MISMATCH", "diagnostic belongs to another session")
    records = request.effects if effect_id else request.dispatches
    record = next(
        (
            value
            for value in records
            if value.job_id == snapshot.ref.job_id
            and value.run_id == snapshot.ref.run_id
            and (not effect_id or value.effect_id == effect_id)
        ),
        None,
    )
    if (
        record is None
        or record.execution.request_id != request.request_id
        or record.execution.session_id != request.session_id
    ):
        raise RequestError("REQUEST_SCOPE_MISMATCH", "diagnostic does not match the original execution")
    payload = {
        "session_id": request.session_id,
        "request_id": request.request_id,
        "job_id": snapshot.ref.job_id,
        "run_id": snapshot.ref.run_id,
        "effect_id": effect_id,
        "dispatch_id": record.execution.dispatch_id,
        "tool": snapshot.specification.display_name or snapshot.specification.job_type,
        "message": SecretRedactor.default().redact_text(message),
    }
    evidence = Evidence(
        f"task-diagnostic:{snapshot.ref.run_id}:{digest(payload)}",
        "task_diagnostic",
        record.execution.request_revision,
        record.execution.item_ids,
        json.dumps(payload, ensure_ascii=False),
        content_digest=digest(payload),
        complete=False,
    )
    return add_evidence(request, evidence, satisfy_items=False)


def recorded_task_diagnostics(request):
    """Project only intact, attributed evidence against its original execution."""
    for evidence in request.evidence:
        if evidence.kind != "task_diagnostic" or evidence.complete:
            continue
        try:
            payload = json.loads(evidence.reference)
        except (ValueError, TypeError):
            continue
        if (
            not isinstance(payload, dict)
            or digest(payload) != evidence.content_digest
            or payload.get("request_id") != request.request_id
            or payload.get("session_id") != request.session_id
        ):
            continue
        effect_id = payload.get("effect_id")
        records = request.effects if effect_id else request.dispatches
        record = next(
            (
                value
                for value in records
                if value.job_id == payload.get("job_id")
                and value.run_id == payload.get("run_id")
                and value.execution.dispatch_id == payload.get("dispatch_id")
                and value.execution.request_revision == evidence.request_revision
                and value.execution.item_ids == evidence.item_ids
                and (not effect_id or value.effect_id == effect_id)
            ),
            None,
        )
        if record is not None:
            yield payload, str(record.status)
