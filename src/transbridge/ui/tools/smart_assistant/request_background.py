"""Deliver short Qt notifications after serialized local application work."""

import logging

logger = logging.getLogger(__name__)


class RequestBackground:
    def __init__(self, binding):
        self.binding = binding

    def submit(self, work, received=None, *, context=None, failed=None, wake=False, settled=None):
        binding = self.binding
        generation = getattr(getattr(binding, "inputs", None), "generation", None)

        def current():
            return not binding._closed and (
                context is None
                or (
                    binding.context == context
                    and generation == getattr(getattr(binding, "inputs", None), "generation", None)
                )
            )

        binding._accepting += 1
        future = binding._queue.submit(work)

        def deliver():
            try:
                result = future.result()
            except Exception as exc:
                logger.warning("Assistant background operation failed", exc_info=True)
                if current():
                    (failed or binding.fail)(exc)
                return
            if not current():
                return
            if received is not None:
                try:
                    received(result)
                except Exception as exc:
                    binding.fail(f"已保存操作的界面交付失败：{exc}")
                    return
            if wake:
                binding.wake()

        def display():
            binding._accepting -= 1
            try:
                deliver()
            finally:
                # Resource cleanup must survive stale-context and closed-view
                # filtering. This callback must not dispatch UI actions.
                if settled is not None:
                    settled()

        def completed(_future):
            try:
                binding.delivered.emit(display)
            except RuntimeError:
                logger.info("Application operation finished after the request view was destroyed")

        future.add_done_callback(completed)
        return future
