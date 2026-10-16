"""End the visible conversation round and commit its history boundary off the GUI thread."""

from transbridge.application.assistant_requests.conversation_visibility import clear_conversation


def clear_chat(facade):
    binding = facade._request_binding
    if binding is not None and binding.inputs.clearing:
        return
    history = facade._conversation.get_transcript()
    if facade._input_actions is not None:
        facade._input_actions.close_skill_preview()
    if facade._submission is not None:
        facade._submission.invalidate()
    if binding is not None:
        binding.inputs.invalidate()
        binding.inputs.clearing = True
        binding.management.stop_generation()
    if facade._confirmation_view is not None:
        facade._confirmation_view.invalidate_pending()
    facade._orchestrator.cancel_current_round()
    if facade._plan_execution is not None:
        facade._plan_execution.interrupt_round()
    if facade._react_execution is not None:
        facade._react_execution.abort()
    facade._controller.handle_abort()
    facade._orchestrator.reset_state()
    if facade._message_list is not None:
        facade._message_list.hide_thinking()
    if facade._streaming_presenter is not None:
        facade._streaming_presenter.advance_generation()

    def clear_view():
        if facade._message_list is not None:
            facade._message_list.clear()
        facade._conversation.clear()
        facade._upload_binding.clear()
        facade._retry_btn = None

    if binding is None:
        clear_view()
        return
    context, generation = binding.context, binding.inputs.generation

    def current():
        return not binding._closed and binding.context == context and binding.inputs.generation == generation

    def cleared(_state):
        if current():
            clear_view()
            binding.inputs.clearing = False
            binding.inputs.failures.pop(context.session_id, None)
            binding.refresh()

    def failed(error):
        if current():
            binding.inputs.clearing = False
            facade.add_system_message(f"清空未能保存，原对话已保留，请重试：{error}")

    binding.background.submit(
        lambda: clear_conversation(binding.service, context, history), cleared, context=context, failed=failed
    )
