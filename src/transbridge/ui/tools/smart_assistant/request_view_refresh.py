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
        future = binding._queue.submit(binding.service.state, context)

        def display():
            self._pending = False
            again, self._again = self._again, False
            if binding._closed:
                return
            try:
                state = future.result()
                if binding.context == context:
                    binding.view.display(binding.service.requests(state))
                    pending = sum(
                        r["status"] == "needs_clarification"
                        for entry in state.get("batches", ())
                        for r in entry["batch"].get("receipts", ())
                    )
                    binding.view.set_pending(
                        pending, sum(b.get("status") in {"routing", "user_paused"} for b in state.get("batches", ()))
                    )
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
