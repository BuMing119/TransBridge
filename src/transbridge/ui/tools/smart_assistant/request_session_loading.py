"""Separate read-only request previews from activation and recovery side effects."""

from copy import deepcopy
from dataclasses import replace

from transbridge.application.assistant_requests.conversation_visibility import visible_history
from transbridge.application.assistant_requests.routing_recovery import unresolved_directives

from .session_execution_access import execution_allowed
from .submission_scope import saved_scopes


class RequestSessionLoading:
    def __init__(self, binding):
        self.binding = binding
        self._preview_state = None

    @property
    def preview(self):
        return self._preview_state is not None

    def load(self, values):
        binding = self.binding
        preview = bool(values.get("_preview"))
        self._preview_state = deepcopy(values.get("assistant_state") or {}) if preview else None
        binding.inputs.invalidate()
        binding.view.display(())
        binding.interrupt()
        if binding.context is not None:
            old_context = binding.context
            binding.background.submit(
                lambda: binding.service.scheduler.deactivate(old_context.session_id, binding.view_id)
            )
        current = binding.facade._session_runtime.request_context()
        binding.context = replace(
            current,
            session_id=values.get("session_id", current.session_id),
            project_id=values.get("project_id", current.project_id),
            variant_id=values.get("variant_id", current.variant_id),
        )
        if not binding.context.session_id:
            return
        if preview:
            self.refresh_preview()
            return
        context = binding.context
        generation = binding.inputs.generation
        history = binding.facade._conversation.get_transcript()

        def current_scope():
            return (
                not binding._closed
                and binding.context == context
                and binding.inputs.generation == generation
                and execution_allowed(binding.facade)
            )

        def restore():
            from transbridge.application.assistant_requests.control_operations import recover_control_calls
            from transbridge.persistence.v2.ids import SessionId, SessionRef

            if not current_scope():
                return None
            binding.service.scheduler.activate(context.session_id, binding.view_id)
            binding.service.ensure_recovered(context)
            recover_control_calls(binding.service, context)
            if not current_scope():
                return None
            binding.service.save_history(context, history)
            snapshot = binding.service.lifecycle.read_session(SessionRef(SessionId(context.session_id)), context)
            return visible_history(snapshot.backend_messages(), snapshot.assistant_data())

        def restored(records):
            if records is None or not current_scope():
                return
            binding.facade._conversation.merge_saved_records(records)
            binding.inputs.present_failures()
            binding.refresh()

        binding.background.submit(restore, restored, context=context, wake=True)

    def refresh_preview(self):
        """Render saved request records without observing or restarting task handles."""
        if self._preview_state is None:
            return False
        binding = self.binding
        state = self._preview_state
        requests = binding.service.requests(state)
        scopes, message_scopes = saved_scopes(state, requests)
        unresolved = tuple(unresolved_directives(state))
        binding.view.set_scopes(scopes, message_scopes)
        binding.view.context_waits = state.get("context_waits", {})
        binding.view.display(requests)
        binding.view.set_pending(
            len(unresolved),
            sum(batch.get("status") in {"routing", "user_paused"} for batch in state.get("batches", ())),
            sum(kind == "replan" for _, _, kind in unresolved),
        )
        binding.view.show_undo("", available=False)
        binding.view.show()
        return True
