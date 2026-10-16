"""Present context waiting separately from business failure and task status."""

from transbridge.application.assistant_context.models import PreparationWait
from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.turns import reject_routing_input
from transbridge.application.security import diagnostic_exception_text
from transbridge.smart_assistant.context_budget import ContextBudgetExceeded


def preparation_failed(binding, error):
    if isinstance(error, ContextBudgetExceeded) and binding.batch is not None:
        context, batch, admission = binding.context, binding.batch, binding.admission
        binding._user_stopped = True

        def current():
            return binding.context == context and binding.admission == admission

        def reject():
            reject_routing_input(binding.service, context, admission, batch, code=error.code)

        def rejected(_result):
            if not current():
                return
            binding.release()
            binding.facade.add_system_message(
                f"{error}\n本批 {len(batch.sources)} 条输入均未发送给模型，原文与附件仍保留在历史中。"
                "请移除或缩小附件，拆分输入后重新发送。"
            )
            binding.refresh()

        def failed(exc):
            if current():
                binding.fail(f"输入拒绝状态未能保存，请重试：{exc}")

        binding.background.submit(reject, rejected, context=context, failed=failed)
        return
    if not isinstance(error, PreparationWait):
        binding.fail(str(error) if isinstance(error, RequestError) else diagnostic_exception_text(error))
        return
    binding.release()
    orchestrator = binding.facade._orchestrator
    orchestrator._on_thinking_indicator_hide()
    orchestrator._on_system_message(
        f"上下文准备暂停：{str(error).rstrip('。')}。处理上述原因后，可点击任务的“重试上下文”。"
    )
    binding.refresh()
