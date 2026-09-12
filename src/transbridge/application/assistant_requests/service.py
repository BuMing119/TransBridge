"""Persistent request commands shared by foreground and background consumers."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import RLock
from uuid import uuid4

from transbridge.application.contracts import RequestContext
from transbridge.persistence.v2.ids import SessionId, SessionRef

from .models import RequestError, UserRequest, digest
from .reducer import RequestEvent, reduce_request


class RequestService:
    """Persist accepted commands before exposing them; never depend on an active view."""

    def __init__(self, lifecycle, *, transcript_store=None):
        from .scheduler import RequestScheduler

        self.lifecycle = lifecycle
        self.transcript_store = transcript_store
        self.scheduler = RequestScheduler()
        self._lock = RLock()
        self._listeners: list[Callable[[str], None]] = []
        self._closed = False
        self._recovered_sessions: set[str] = set()

    def subscribe(self, callback: Callable[[str], None]) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    @contextmanager
    def serialized(self, context: RequestContext):
        """Order request changes with local commit checks; never hold across network waits."""
        with self._lock:
            if self._closed or not context.session_id:
                raise RequestError("ADMISSION_CLOSED", "request service is unavailable")
            yield

    def transact(
        self, context: RequestContext, change: Callable[[dict], None], *, history=None, append_messages=()
    ) -> dict:
        if not context.session_id:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "a saved Session is required")
        with self._lock:
            if self._closed:
                raise RequestError("REQUEST_SERVICE_CLOSED", "the application is closing")
            command_errors = []

            def update(snapshot):
                state = snapshot.assistant_data()
                try:
                    change(state)
                except RequestError as exc:
                    command_errors.append(exc)
                    raise
                changes = {"assistant_state": state}
                records = history
                if append_messages:
                    records = list(snapshot.backend_messages())
                    known = {m.get("message_id") for m in records}
                    records.extend(m for m in append_messages if m.get("message_id") not in known)
                try:
                    if records is not None:
                        changes["messages"] = records
                        return self.with_transcript(snapshot, records, **changes)
                    return replace(snapshot, **changes)
                except RequestError as exc:
                    command_errors.append(exc)
                    raise

            result = self.lifecycle.transact(SessionRef(SessionId(context.session_id)), context, update, publish=False)
            if not result.is_success or result.value is None:
                if command_errors:
                    raise command_errors[0]
                detail = "; ".join(f"{d.code}: {d.message}" for d in result.diagnostics)
                raise RequestError("ADMISSION_PERSIST_FAILED", detail or "Session command was not saved")
            state = result.value.assistant_data()
        return state

    def state(self, context: RequestContext) -> dict:
        if not context.session_id:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "a saved Session is required")
        return self.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context).assistant_data()

    @staticmethod
    def requests(state: dict) -> tuple[UserRequest, ...]:
        return tuple(UserRequest.from_dict(value) for value in state.get("requests", ()))

    def accept_input(
        self, context: RequestContext, text: str, *, selection: dict, command_id: str | None = None
    ) -> dict:
        command_id = command_id or uuid4().hex
        content_hash = digest({"text": text, "context": context.to_dict(), "selection": selection})
        accepted = {}

        def apply(state):
            if state.get("session_tombstone"):
                raise RequestError("SESSION_DELETING", "会话正在删除，不能接纳新输入。")
            ingress = state.setdefault("ingress", [])
            old = next((value for value in ingress if value["message_id"] == command_id), None)
            if old:
                if old["digest"] != content_hash:
                    raise RequestError("COMMAND_PAYLOAD_CONFLICT", "input identity reused")
                accepted.update(old)
                return
            item = {
                "message_id": command_id,
                "text": text,
                "sequence": len(ingress) + 1,
                "context": context.to_dict(),
                "selection": deepcopy(selection),
                "digest": content_hash,
                "status": "pending",
                "batch_id": "",
            }
            ingress.append(item)
            accepted.update(item)

        self.transact(context, apply, append_messages=({"role": "user", "content": text, "message_id": command_id},))
        return accepted

    def _append_transcript(self, snapshot, records):
        from .transcript import TranscriptManifest, TranscriptMessage

        manifest = TranscriptManifest.from_dict(snapshot.transcript_data())
        existing = self.transcript_store.read(snapshot.ref.identity.value, manifest)
        known = {message.message_id: message for message in existing}
        additions = []
        for index, message in enumerate(records):
            message_id = message.get("message_id") or f"legacy-{index}-{digest(message)}"
            if message_id in known:
                previous = known[message_id]
                old = (
                    {
                        "role": previous.role,
                        "content": previous.content,
                        "tool_call_id": previous.tool_call_id,
                        "tool_calls": [],
                    }
                    if not previous.tool_calls and isinstance(previous.content, (str, type(None)))
                    else previous.to_dict()
                )
                if any(
                    old.get(key) != message.get(key, [] if key == "tool_calls" else None)
                    for key in ("role", "content", "tool_call_id", "tool_calls")
                ):
                    raise RequestError("COMMAND_PAYLOAD_CONFLICT", "immutable message content changed")
                continue
            additions.append(
                TranscriptMessage(
                    message_id,
                    len(existing) + len(additions) + 1,
                    message["role"],
                    message.get("content", ""),
                    tool_call_id=message.get("tool_call_id"),
                    tool_calls=tuple(message.get("tool_calls", ())),
                    origin="user" if message["role"] == "user" else "runtime",
                    metadata={
                        key: value
                        for key, value in message.items()
                        if key not in {"message_id", "role", "content", "tool_calls", "tool_call_id"}
                    },
                )
            )
            known[message_id] = additions[-1]
        return self.transcript_store.append(snapshot.ref.identity.value, manifest, tuple(additions))

    def with_transcript(self, snapshot, records, **changes):
        """Stage complete evidence on a snapshot; the caller commits it once.

        This performs attachment writes only. The Session manifest and visible
        state remain uncommitted until the enclosing lifecycle transaction saves.
        Legacy messages gain stable IDs in the backend mirror for later restore.
        """
        if self.transcript_store is None:
            return replace(snapshot, backend_history=tuple(records), **changes)
        normalized = [
            {**record, "message_id": record.get("message_id") or f"legacy-{index}-{digest(record)}"}
            for index, record in enumerate(records)
        ]
        # A stale UI projection must not erase another admitted input during a
        # successful CAS reload. Keep prior missing records before new material.
        provided_ids = {m["message_id"] for m in normalized}
        previous = snapshot.backend_messages()
        missing = [m for m in previous if m.get("message_id") and m["message_id"] not in provided_ids]
        if missing:
            old_ids = {m.get("message_id") for m in previous}
            first_new = next((i for i, m in enumerate(normalized) if m["message_id"] not in old_ids), len(normalized))
            normalized[first_new:first_new] = missing
        manifest = self._append_transcript(snapshot, normalized)
        normalized = snapshot.freeze_history(normalized)
        if changes.get("messages") is records:
            changes["messages"] = normalized
        return replace(snapshot, backend_history=normalized, transcript_manifest=manifest.to_dict(), **changes)

    def save_history(self, context: RequestContext, records: list[dict], *, request_id: str | None = None) -> None:
        def apply(state):
            if request_id:
                if not any(r.request_id == request_id for r in self.requests(state)):
                    raise RequestError("REQUEST_SCOPE_MISMATCH", "unknown request result owner")
                owners = state.setdefault("result_owners", {})
                message_owners = state.setdefault("message_owners", {})
                recorded = set(state.get("recorded_ids", ()))
                for record in records:
                    if record.get("role") in {"assistant", "tool"} and record.get("message_id") not in recorded:
                        message_owners[record["message_id"]] = request_id
                    is_result = record.get("role") == "tool" or (
                        record.get("role") == "user"
                        and str(record.get("content", "")).startswith((
                            "[Tool result - ",
                            "【工具执行结果",
                            "[Plan execution completed]",
                        ))
                    )
                    if (
                        is_result
                        and record.get("message_id") not in recorded
                        and record.get("message_id") not in owners
                    ):
                        owners[record["message_id"]] = request_id
            state["recorded_ids"] = [r["message_id"] for r in records if r.get("message_id")]

        self.transact(context, apply, history=records)

    def read_result(self, context: RequestContext, request_id: str, message_id: str, *, offset=0, limit=2000) -> dict:
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 8000:
            raise RequestError("REQUEST_PROTOCOL_INVALID", "invalid result range")
        if not context.session_id:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "a saved Session is required")
        snapshot = self.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
        state = snapshot.assistant_data()
        if state.get("result_owners", {}).get(message_id) != request_id:
            raise RequestError("REQUEST_SCOPE_MISMATCH", "result does not belong to this request")
        records = snapshot.backend_messages()
        if self.transcript_store is not None:
            from .transcript import TranscriptManifest

            manifest = TranscriptManifest.from_dict(snapshot.transcript_data())
            if manifest.segments:
                records = tuple(m.to_dict() for m in self.transcript_store.read(context.session_id, manifest))
        message = next((m for m in records if m.get("message_id") == message_id), None)
        if message is None:
            raise RequestError("ARTIFACT_UNAVAILABLE", "the original result is unavailable")
        content = str(message.get("content", ""))
        return {
            "message_id": message_id,
            "offset": offset,
            "total": len(content),
            "text": content[offset : offset + limit],
        }

    def ensure_recovered(self, context: RequestContext, *, live_job_ids=(), proven_outcomes=None) -> dict:
        """Revalidate persisted request authority once per service/Session lifetime."""
        from .management import recover_session_requests

        with self._lock:
            current = self.state(context)
            if context.session_id in self._recovered_sessions:
                return current
            if current.get("requests"):

                def apply(state):
                    recovered = recover_session_requests(state, live_job_ids, proven_outcomes)
                    state.clear()
                    state.update(recovered)

                current = self.transact(context, apply)
            self._recovered_sessions.add(context.session_id)
            return current

    def _manage(self, context, kind, payload, *, text, selection, command_id):
        from .management import apply_management_command
        from .routing import RoutingSource

        command_id = command_id or uuid4().hex
        source = RoutingSource(
            command_id,
            text,
            tuple(
                sorted(
                    (key, str(value))
                    for key, value in context.to_dict().items()
                    if key in {"owner_id", "session_id", "project_id", "variant_id"} and value is not None
                )
            ),
        )
        result_ids = []

        def apply(state):
            result_ids.clear()
            if state.get("session_tombstone"):
                raise RequestError("SESSION_DELETING", "会话正在删除，不能创建或调整请求。")
            result_ids.extend(
                apply_management_command(
                    state,
                    kind=kind,
                    source=source,
                    context_data=context.to_dict(),
                    selection=selection or {},
                    payload=payload,
                )
            )

        state = self.transact(
            context, apply, append_messages=({"message_id": command_id, "role": "user", "content": text},)
        )
        self.notify(context.session_id)
        return state, tuple(result_ids)

    def clarify(self, context, batch_id, local_id, target_request_id, *, text, selection=None, command_id=None) -> dict:
        """Atomically accept a user clarification and apply only its unresolved directive."""
        state, _ = self._manage(
            context,
            "clarify",
            {
                "batch_id": batch_id,
                "local_id": local_id,
                "target_request_id": target_request_id,
            },
            text=text,
            selection=selection,
            command_id=command_id,
        )
        return state

    def restart(self, context, request_id, *, text, selection=None, command_id=None) -> UserRequest:
        """Create a fresh successor for an explicit user continuation of a terminal request."""
        state, ids = self._manage(
            context, "restart", {"request_id": request_id}, text=text, selection=selection, command_id=command_id
        )
        return next(request for request in self.requests(state) if request.request_id == ids[0])

    def reassign(
        self,
        context,
        source_request_id,
        target_request_id,
        item_id,
        *,
        expected_source_revision,
        expected_target_revision,
        text,
        selection=None,
        command_id=None,
    ) -> tuple[UserRequest, UserRequest]:
        """Atomically correct untouched item ownership with its accepted user source."""
        state, ids = self._manage(
            context,
            "reassign",
            {
                "source_request_id": source_request_id,
                "target_request_id": target_request_id,
                "item_id": item_id,
                "expected_source_revision": expected_source_revision,
                "expected_target_revision": expected_target_revision,
            },
            text=text,
            selection=selection,
            command_id=command_id,
        )
        by_id = {request.request_id: request for request in self.requests(state)}
        return by_id[ids[0]], by_id[ids[1]]

    def prepare_batch(self, context: RequestContext):
        from .routing import RoutingBatch, RoutingSource

        batch_result = []
        current = self.state(context)
        if current.get("session_tombstone"):
            return None
        if not any(i.get("status") == "pending" for i in current.get("ingress", ())) and not any(
            b.get("status") == "routing" for b in current.get("batches", ())
        ):
            return None

        def apply(state):
            batch_result.clear()
            batches = state.setdefault("batches", [])
            existing = next((b for b in batches if b.get("status") == "routing"), None)
            if existing:
                batch_result.append(RoutingBatch.from_dict(existing["batch"]))
                return
            inputs = [i for i in state.get("ingress", ()) if i["status"] == "pending"]
            if not inputs:
                return
            sources = tuple(
                RoutingSource(
                    i["message_id"],
                    i["text"],
                    tuple(
                        sorted(
                            (k, str(v))
                            for k, v in i["context"].items()
                            if k in {"owner_id", "session_id", "project_id", "variant_id"} and v is not None
                        )
                    ),
                )
                for i in inputs
            )
            batch = RoutingBatch(uuid4().hex, context.session_id, sources)
            batches.append({"batch": batch.to_dict(), "status": "routing"})
            for item in inputs:
                item.update(status="routing", batch_id=batch.batch_id)
            batch_result.append(batch)

        self.transact(context, apply)
        return batch_result[0] if batch_result else None

    def apply_routing(self, context: RequestContext, batch_id: str, proposal: dict) -> dict:
        from .routing import RoutingBatch, apply_proposal, parse_proposal

        def apply(state):
            batch = next((b for b in state.get("batches", ()) if b["batch"]["batch_id"] == batch_id), None)
            if state.get("session_tombstone"):
                raise RequestError("SESSION_DELETING", "会话正在删除，不能应用迟到的路由。")
            if batch is None:
                raise RequestError("REQUEST_PROTOCOL_INVALID", "unknown routing batch")
            result = apply_proposal(
                self.requests(state), RoutingBatch.from_dict(batch["batch"]), parse_proposal(proposal)
            )
            state["requests"] = [r.to_dict() for r in result.requests]
            batch.update(batch=result.batch.to_dict(), status="applied")
            for item in state.get("ingress", ()):
                if item["batch_id"] == batch_id:
                    item["status"] = "applied"

        state = self.transact(context, apply)
        self.notify(context.session_id)
        return state

    def update_request(
        self, context: RequestContext, request_id: str, update: Callable[[UserRequest], UserRequest], *, history=None
    ) -> UserRequest:
        updated = []

        def apply(state):
            requests = list(self.requests(state))
            for index, request in enumerate(requests):
                if request.request_id == request_id:
                    replacement = update(request)
                    if replacement.request_id != request_id or replacement.session_id != context.session_id:
                        raise RequestError("REQUEST_SCOPE_MISMATCH", "request ownership cannot change")
                    requests[index] = replacement
                    updated.append(replacement)
                    break
            else:
                raise RequestError("REQUEST_TARGET_AMBIGUOUS", "request does not exist in this Session")
            state["requests"] = [r.to_dict() for r in requests]

        self.transact(context, apply, history=history)
        return updated[-1]

    def command(self, context: RequestContext, request_id: str, kind: str, revision: int, **payload) -> UserRequest:
        event = RequestEvent(uuid4().hex, kind, revision, payload)
        updated = self.update_request(context, request_id, lambda request: reduce_request(request, event))
        self.notify(context.session_id)
        return updated

    def notify(self, session_id: str) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(session_id)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            events = getattr(self, "request_task_events", None)
            if events is not None:
                events.close()
            self.scheduler.close()
            self._listeners.clear()
