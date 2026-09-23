"""Select and persist a foreground turn without waiting on storage in Qt."""

from transbridge.application.assistant_context.admission import available_requests
from transbridge.application.assistant_requests.turns import accept_turn
from transbridge.smart_assistant.context_runtime import configuration_digest
from transbridge.smart_assistant.native_tools import build_native_tool_definitions


def select_next(binding):
    context, generation = binding.context, binding._turn_generation
    cfg = configuration_digest(getattr(binding.facade._orchestrator, "_cached_llm_config", None))
    tools = build_native_tool_definitions(
        binding.facade._conversation.get_loaded_tool_namespaces(), request_stage="execution"
    )
    priority = binding._priority

    def select():
        router = getattr(binding.service, "request_task_events", None)
        if router is not None:
            router.retry_pending(context.session_id)
        batch = binding.service.prepare_batch(context)
        if batch is not None:
            return batch, binding.service.scheduler.acquire_routing(context.session_id, binding.view_id)
        state = binding.service.state(context)
        requests = available_requests(state, binding.service.requests(state), cfg)
        selection = binding.service.scheduler.select_next_turn(
            context.session_id,
            binding.view_id,
            requests,
            priority_request_ids=priority,
            allowed_tools=tuple(tool.name for tool in tools),
        )
        if selection is not None:
            accept_turn(binding.service, context, selection)
        return None, selection.admission if selection is not None else None

    def received(result):
        batch, admission = result
        if generation != binding._turn_generation or not binding._active or binding._user_stopped:
            if admission is not None:
                binding.background.submit(lambda: binding.service.scheduler.release(admission), wake=True)
            return
        if admission is None:
            binding.refresh()
            return
        binding.batch, binding.admission = batch, admission
        if batch is None:
            binding._priority = ()
            binding._set_gate()
        binding.facade._controller.handle_round_interrupted()
        binding.facade._controller.handle_user_message("")
        binding.refresh()

    # Always deliver selection: a switched view must release an acquired lease.
    binding.background.submit(select, received)
