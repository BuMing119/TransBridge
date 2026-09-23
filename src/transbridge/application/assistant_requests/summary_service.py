"""Read request history and validate legacy summary material without publishing it."""

import logging

from transbridge.persistence.v2.ids import SessionId, SessionRef

from .history_scope import assign_history_requests
from .models import digest
from .summaries import RequestSummary, validate_summary
from .transcript import TranscriptManifest

logger = logging.getLogger(__name__)


class RequestSummaryService:
    def __init__(self, requests):
        self.requests = requests

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
