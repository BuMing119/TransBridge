"""Deliver short Qt notifications after serialized local application work."""

import logging

logger = logging.getLogger(__name__)


class RequestBackground:
    def __init__(self, binding):
        self.binding = binding

    def submit(self, work, received=None, *, context=None, failed=None, wake=False):
        binding = self.binding
        binding._accepting += 1
        future = binding._queue.submit(work)

        def display():
            binding._accepting -= 1
            try:
                result = future.result()
            except Exception as exc:
                logger.warning("Assistant background operation failed", exc_info=True)
                if not binding._closed and (context is None or binding.context == context):
                    (failed or binding.fail)(exc)
                return
            if binding._closed or (context is not None and binding.context != context):
                return
            if received is not None:
                try:
                    received(result)
                except Exception as exc:
                    binding.fail(f"已保存操作的界面交付失败：{exc}")
                    return
            if wake:
                binding.wake()

        def completed(_future):
            try:
                binding.delivered.emit(display)
            except RuntimeError:
                logger.info("Application operation finished after the request view was destroyed")

        future.add_done_callback(completed)
        return future
