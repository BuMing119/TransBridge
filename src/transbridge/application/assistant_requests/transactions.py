"""Publish a request command and its lifecycle evidence in one Session revision."""

from dataclasses import replace

from transbridge.persistence.v2.ids import SessionId, SessionRef

from .archival import compact_request_state, hydrate_request_state
from .journal import EventCause, append_events, observe_state
from .models import RequestError
from .transcript import TranscriptManifest


def commit_request_change(
    lifecycle,
    context,
    change,
    with_transcript,
    *,
    history=None,
    append_messages=(),
    cause=None,
    transcript_store=None,
    artifact_refs=(),
):
    command_errors = []
    transaction_errors = []

    def update(snapshot):
        command_errors.clear()
        transaction_errors.clear()
        state = snapshot.assistant_data()
        if state.get("request_archives"):
            state = hydrate_request_state(state, context.session_id, transcript_store)
        before = observe_state(state)
        try:
            try:
                change(state)
            except RequestError as exc:
                if cause is None or cause.operation != "routing.applied":
                    raise
                # Reject the entire candidate mutation, then publish only the
                # rejection fact. This is not an acknowledgement of the proposal.
                state = snapshot.assistant_data()
                if state.get("request_archives"):
                    state = hydrate_request_state(state, context.session_id, transcript_store)
                command_errors.append(exc)
                rejected = EventCause(
                    "routing.rejected",
                    "model",
                    cause.references,
                    {**cause.details, "code": exc.code},
                    record_unchanged=True,
                )
                append_events(state, before, context.session_id, rejected)
            else:
                append_events(state, before, context.session_id, cause)
            changes = {"assistant_state": state}
            records = history
            if append_messages:
                records = list(snapshot.backend_messages())
                known = {m.get("message_id") for m in records}
                records.extend(m for m in append_messages if m.get("message_id") not in known)
            if records is not None:
                changes["messages"] = records
                prepared = with_transcript(snapshot, records, **changes)
            else:
                prepared = replace(snapshot, **changes)
            if artifact_refs:
                manifest = TranscriptManifest.from_dict(prepared.transcript_data())
                artifacts = tuple(dict.fromkeys((*manifest.artifacts, *artifact_refs)))
                prepared = replace(prepared, transcript_manifest=replace(manifest, artifacts=artifacts).to_dict())
            if transcript_store is not None and (
                state.get("request_archives")
                or sum(r.get("status", "open") not in {"open", "stopping"} for r in state.get("requests", ())) > 100
            ):
                compacted, manifest = compact_request_state(
                    state,
                    TranscriptManifest.from_dict(prepared.transcript_data()),
                    context.session_id,
                    transcript_store,
                )
                prepared = replace(prepared, assistant_state=compacted, transcript_manifest=manifest.to_dict())
            return prepared
        except RequestError as exc:
            command_errors.append(exc)
            transaction_errors.append(exc)
            raise

    result = lifecycle.transact(SessionRef(SessionId(context.session_id)), context, update, publish=False)
    if not result.is_success or result.value is None:
        if transaction_errors:
            raise transaction_errors[-1]
        detail = "; ".join(f"{d.code}: {d.message}" for d in result.diagnostics)
        raise RequestError("ADMISSION_PERSIST_FAILED", detail or "Session command was not saved")
    if command_errors:
        raise command_errors[0]
    state = result.value.assistant_data()
    return (
        hydrate_request_state(state, context.session_id, transcript_store) if state.get("request_archives") else state
    )
