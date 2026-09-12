"""Generate derived material outside the command lock and publish only current results."""

from dataclasses import replace
import logging

from transbridge.persistence.v2.ids import SessionId, SessionRef

from .history_scope import assign_history_requests
from .models import RequestError, digest
from .summaries import RequestSummary, plan_summary, validate_summary
from .transcript import TranscriptManifest

logger = logging.getLogger(__name__)


class RequestSummaryService:
    def __init__(self, requests):
        self.requests = requests

    def refresh(self, context, request_id):
        """Called by a worker; a stale result is discarded rather than retried as authority."""
        ref = SessionRef(SessionId(context.session_id))
        snapshot = self.requests.lifecycle.read_session(ref, context)
        state = snapshot.assistant_data()
        request = next((r for r in self.requests.requests(state) if r.request_id == request_id), None)
        if request is None or request.terminal or state.get("session_tombstone"):
            return None
        history = self._history(snapshot, state)
        summary = plan_summary(request, history)
        if summary is None:
            return None
        if state.get("request_summaries", {}).get(request_id) == summary.to_dict():
            return summary
        published = []

        def publish(current):
            published.clear()
            current_state = current.assistant_data()
            current_request = next(
                (r for r in self.requests.requests(current_state) if r.request_id == request_id), None
            )
            if (
                current_request is None
                or current_request.terminal
                or current_state.get("session_tombstone")
                or not validate_summary(summary, current_request, self._history(current, current_state))
            ):
                return current
            current_state.setdefault("request_summaries", {})[request_id] = summary.to_dict()
            published.append(summary)
            return replace(current, assistant_state=current_state)

        with self.requests.serialized(context):
            result = self.requests.lifecycle.transact(ref, context, publish, publish=False)
        if not result.is_success:
            detail = "; ".join(f"{d.code}: {d.message}" for d in result.diagnostics)
            raise RequestError("SUMMARY_PERSIST_FAILED", detail or "Derived summary was not saved")
        return published[-1] if published else None

    def _history(self, snapshot, state):
        records = snapshot.backend_messages()
        manifest = TranscriptManifest.from_dict(snapshot.transcript_data())
        if self.requests.transcript_store is not None and manifest.segments:
            records = []
            for message in self.requests.transcript_store.read(snapshot.ref.identity.value, manifest):
                value = message.to_dict()
                record = {
                    **value["metadata"],
                    "message_id": message.message_id,
                    "sequence": message.sequence,
                    "role": message.role,
                    "content": value["content"],
                }
                for key in ("tool_calls", "tool_call_id", "request_id", "request_revision"):
                    if value.get(key):
                        record[key] = value[key]
                records.append(record)
        return assign_history_requests(
            records,
            self.requests.requests(state),
            {**state.get("result_owners", {}), **state.get("message_owners", {})},
        )

    def prepared_material(self, context, request_id):
        """Validate against one saved history off the GUI thread; return its exact projection."""
        snapshot = self.requests.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
        state = snapshot.assistant_data()
        request = next((r for r in self.requests.requests(state) if r.request_id == request_id), None)
        history = self._history(snapshot, state)
        if request is None:
            return history, None, ""
        return history, self.read_valid(state, request, history), digest(request.to_dict())

    @staticmethod
    def read_valid(state, request, history):
        stored = state.get("request_summaries", {}).get(request.request_id)
        if stored is None:
            return None
        try:
            summary = RequestSummary.from_dict(stored)
            return summary if validate_summary(summary, request, history) else None
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring invalid derived summary for request %s", request.request_id, exc_info=True)
            return None
