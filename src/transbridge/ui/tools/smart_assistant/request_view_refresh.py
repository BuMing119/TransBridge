"""Coalesced request-list reads must not wait on Session I/O in the Qt thread."""

import logging

logger = logging.getLogger(__name__)


class RequestViewRefresh:
    def __init__(self, binding):
        self.binding = binding
        self._pending = False
        self._again = False
        self._context = None

    def request(self):
        binding = self.binding
        if binding._closed or binding.context is None:
            return
        if binding.context != self._context:
            self._context = binding.context
            binding.view.hide()
        if self._pending:
            self._again = True
            return
        context = binding.context
        self._pending = True

        def read_projection():
            state = binding.service.state(context)
            requests = binding.service.requests(state)
            pending = sum(
                receipt["status"] == "needs_clarification"
                for entry in state.get("batches", ())
                for receipt in entry["batch"].get("receipts", ())
            )
            routing = sum(batch.get("status") in {"routing", "user_paused"} for batch in state.get("batches", ()))
            return requests, pending, routing

        future = binding._queue.submit(read_projection)

        def display():
            self._pending = False
            again, self._again = self._again, False
            if binding._closed:
                return
            try:
                requests, pending, routing = future.result()
                if binding.context == context:
                    binding.view.display(requests)
                    binding.undo.observe(requests)
                    binding.view.set_pending(pending, routing)
                    binding.view.show()
            except Exception:
                logger.warning("Unable to refresh request list for its saved Session", exc_info=True)
            if again or binding.context != context:
                self.request()

        def completed(_future):
            try:
                binding.delivered.emit(display)
            except RuntimeError:
                logger.info("Request-list read finished after the view was destroyed")

        future.add_done_callback(completed)
