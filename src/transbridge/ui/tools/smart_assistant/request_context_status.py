"""Present context waiting separately from business failure and task status."""

from transbridge.application.assistant_context.models import PreparationWait


def preparation_failed(binding, error):
    if not isinstance(error, PreparationWait):
        binding.fail(str(error))
        return
    binding.release()
    orchestrator = binding.facade._orchestrator
    orchestrator._on_thinking_indicator_hide()
    orchestrator._on_system_message(f"上下文准备暂停：{error}。可调整容量或点击请求的继续按钮重试。")
    binding.refresh()
