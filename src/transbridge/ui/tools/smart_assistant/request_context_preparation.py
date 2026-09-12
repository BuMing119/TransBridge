"""Background preparation of derived request material, fenced by the current turn."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import logging
import os
from threading import Event, Thread

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.scheduler import ready_item_ids
from transbridge.application.assistant_requests.summary_service import RequestSummaryService
from transbridge.smart_assistant.request_model_input import RequestModelInput

logger = logging.getLogger(__name__)


class RequestContextPreparation:
    def __init__(self, binding):
        self.binding = binding
        self.summaries = RequestSummaryService(binding.service)
        self._cancelled = Event()
        self._summary_client = None
        self._queue = ThreadPoolExecutor(max_workers=2, thread_name_prefix="assistant-context")

    def cancel(self):
        self._cancelled.set()
        if self._summary_client is not None:
            client = self._summary_client
            self._summary_client = None
            Thread(target=client.cancel, daemon=True, name="assistant-summary-cancel").start()

    def close(self):
        self.cancel()
        self._queue.shutdown(wait=False, cancel_futures=True)

    def prepare(self, history, max_tokens, *, context_window, on_ready, on_error):
        binding = self.binding
        admission, context = binding.admission, binding.context
        self._cancelled.set()
        cancelled = self._cancelled = Event()

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

                if prepared.request is None:
                    work = prepared.assemble
                else:
                    from transbridge.smart_assistant.context_runtime import ContextRuntime

                    orchestrator = binding.facade._orchestrator
                    from transbridge.smart_assistant.context_summary import IsolatedSummaryClient

                    config = (
                        orchestrator._cached_llm_config.copy_for_execution()
                        if hasattr(orchestrator._cached_llm_config, "copy_for_execution")
                        else deepcopy(orchestrator._cached_llm_config)
                    )
                    client = IsolatedSummaryClient(config, orchestrator.get_llm_client())
                    self._summary_client = client
                    collector = getattr(orchestrator, "_obs_collector", None)
                    from transbridge.config.paths import get_config_file_path

                    config_path = get_config_file_path()
                    config_stamp = getattr(orchestrator, "_llm_config_mtime", None)

                    def still_current():
                        if cancelled.is_set():
                            return False
                        if config_stamp is None:
                            return True
                        try:
                            return os.path.getmtime(config_path) == config_stamp
                        except OSError:
                            return config_stamp == 0

                    runtime = ContextRuntime(
                        binding.service,
                        context,
                        selected,
                        client=client,
                        config=config,
                        on_usage=(
                            collector.capture_usage_callback() if hasattr(collector, "capture_usage_callback") else None
                        ),
                        still_current=still_current,
                    )

                    def work():
                        try:
                            return runtime.prepare(prepared)
                        finally:
                            client.cancel()

                self._submit(work, assembled)
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
        future = self._queue.submit(work)

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
