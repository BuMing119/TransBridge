"""Present context waiting separately from business failure and task status."""

from transbridge.application.assistant_context.models import PreparationWait
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.security import diagnostic_exception_text


def preparation_failed(binding, error):
    if not isinstance(error, PreparationWait):
        binding.fail(str(error) if isinstance(error, RequestError) else diagnostic_exception_text(error))
        return
    binding.release()
    orchestrator = binding.facade._orchestrator
    orchestrator._on_thinking_indicator_hide()
    orchestrator._on_system_message(
        f"上下文准备暂停：{str(error).rstrip('。')}。"
        "请在设置 → AI 服务中核对助手上下文容量；调整容量或材料后，点击请求的继续按钮重试。"
    )
    binding.refresh()
