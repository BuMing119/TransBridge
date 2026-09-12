"""Background preparation of derived request material, fenced by the current turn."""

import logging

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.scheduler import ready_item_ids
from transbridge.application.assistant_requests.summary_service import RequestSummaryService
from transbridge.smart_assistant.request_model_input import RequestModelInput

logger = logging.getLogger(__name__)


class RequestContextPreparation:
    def __init__(self, binding):
        self.binding = binding
        self.summaries = RequestSummaryService(binding.service)

    def prepare(self, history, max_tokens, *, context_window, on_ready, on_error):
        binding = self.binding
        admission, context = binding.admission, binding.context

        def active(expected):
            return (
                not binding._closed and binding._active and binding.context == context and binding.admission == expected
            )

        def assemble(material):
            if not active(admission):
                return
            try:
                canonical, summary, request_digest = material
                prepared = binding.prepare_model_input(
                    canonical,
                    max_tokens,
                    context_window=context_window,
                    prepared_summary=(summary, request_digest),
                    defer_assembly=True,
                )
                if not isinstance(prepared, RequestModelInput):
                    on_ready(prepared)
                    return
                # ReAct continuation may acquire a replacement lease above. It
                # is this new admission, not the previous round, that owns CPU work.
                selected = binding.admission

                def assembled(result):
                    if not active(selected):
                        return
                    try:
                        if not self._still_admitted(selected, context):
                            binding.release()
                            binding.wake()
                            on_ready(([], ()))
                            return
                        on_ready(result.result())
                    except Exception as exc:
                        on_error(exc)

                self._submit(prepared.assemble, assembled)
            except Exception as exc:
                on_error(exc)

        if admission is None or admission.stage != "execution":
            assemble((history, None, ""))
            return

        def generate():
            with binding.service.serialized(context):
                binding.service.scheduler.validate(admission)
                state = binding.service.state(context)
                request = next(r for r in binding.service.requests(state) if r.request_id == admission.request_id)
                if (
                    request.terminal
                    or request.revision != admission.request_revision
                    or request.lease_epoch != admission.request_lease_epoch
                ):
                    raise RequestError("TURN_LEASE_STALE", "request changed before context evidence was saved")
                # A newly completed tool result or rebuilt system message may not
                # be saved yet. Generation and consumption must share evidence.
                binding.service.save_history(context, history, request_id=admission.request_id)
            try:
                self.summaries.refresh(context, admission.request_id)
            except Exception:
                logger.warning("Request summary unavailable; retaining original context materials", exc_info=True)
            return self.summaries.prepared_material(context, admission.request_id)

        def completed(result):
            if not active(admission):
                return
            try:
                material = result.result()
            except Exception as exc:
                on_error(exc)
            else:
                assemble(material)

        self._submit(generate, completed)

    def _submit(self, work, callback):
        future = self.binding._queue.submit(work)

        def completed(result):
            try:
                self.binding.delivered.emit(lambda: callback(result))
            except RuntimeError:
                logger.info("Context preparation finished after the request view was destroyed")

        future.add_done_callback(completed)

    def _still_admitted(self, admission, context):
        """GUI delivery rechecks authority after expensive detached assembly."""
        binding = self.binding
        try:
            binding.service.scheduler.validate(admission)
        except RequestError as exc:
            if exc.code != "TURN_LEASE_STALE":
                raise
            return False
        state = binding.service.state(context)
        if state.get("session_tombstone"):
            return False
        if admission.stage != "execution":
            return True
        request = next((r for r in binding.service.requests(state) if r.request_id == admission.request_id), None)
        return (
            request is not None
            and not request.terminal
            and not request.pause_reasons
            and request.session_id == admission.session_id
            and request.scope == admission.scope
            and request.revision == admission.request_revision
            and request.lease_epoch == admission.request_lease_epoch
            and set(admission.ready_item_ids) <= set(ready_item_ids(request))
        )
