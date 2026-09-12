"""Persisted, item-scoped approvals and fresh admission for their consumption."""

from copy import deepcopy
from dataclasses import replace
import logging
from uuid import uuid4

from PyQt6.QtCore import QTimer

from transbridge.application.assistant_requests.journal import EventCause
from transbridge.application.assistant_requests.models import ItemStatus, RequestError
from transbridge.application.assistant_requests.reducer import RequestEvent, reduce_request
from transbridge.smart_assistant.native_tools import build_native_tool_definitions

logger = logging.getLogger(__name__)
_APPROVAL_REASONS = frozenset({"approval", "approval_revalidation"})


class RequestConfirmationBinding:
    def __init__(self, binding):
        self.binding = binding

    def capture(self, parsed, *, expected_confirmation=None):
        binding = self.binding
        admission = binding.admission
        if admission is None:
            return
        controller = binding.facade._controller
        binding.service.save_history(
            binding.context, binding.facade._conversation.get_transcript(), request_id=admission.request_id
        )
        if controller.state.value == "awaiting":
            item_ids = self._approval_items(admission)
            record = {
                "revision": admission.request_revision,
                "lease_epoch": admission.request_lease_epoch,
                "confirmation_id": controller.pending_confirmation_id,
                "item_ids": item_ids,
                "parsed": deepcopy(parsed),
            }

            def save(state):
                binding.service.scheduler.validate(admission)
                current = self._request(state, admission.request_id)
                self._require_current(current, admission)
                existing = state.setdefault("confirmations", {}).get(admission.request_id)
                if existing != expected_confirmation:
                    raise RequestError("CONFIRMATION_STALE", "已有另一项待确认操作，请先处理原确认。")
                updated = self._reduce(current, "wait", item_ids=item_ids, reason="approval")
                self._replace_request(state, updated)
                state["confirmations"][admission.request_id] = record

            binding.service.transact(
                binding.context,
                save,
                cause=EventCause("confirmation.presented", references={"request_ids": [admission.request_id]}),
            )
            binding.release(interrupt=False)
            binding.wake()
        elif controller.state.value == "awaiting_task":
            binding.release(interrupt=False)
            binding.wake()

    def restore(self, request_id):
        """Re-present a proposal; restoring a card never grants approval."""
        binding = self.binding
        state = binding.service.state(binding.context)
        saved = state.get("confirmations", {}).get(request_id)
        if not saved:
            return False
        request = self._request(state, request_id)
        if saved.get("revision") != request.revision:
            return False
        if (
            not saved.get("item_ids")
            or not saved.get("confirmation_id")
            or saved.get("lease_epoch") != request.lease_epoch
        ):
            self._repropose_legacy(request, saved)
            return True
        selected = self._select(request, saved)
        if selected is None:
            binding.facade.add_system_message("该操作仍有其他等待条件，条件解除后可再次查看确认。")
            return True
        from .request_confirmation import renew_confirmation

        binding.admission = selected.admission
        try:
            parsed = renew_confirmation(saved["parsed"], binding.facade._conversation)
            binding.facade._controller.restore_confirmation(parsed)
            self.capture(parsed, expected_confirmation=saved)
        except Exception:
            binding.facade._confirmation_view.invalidate_pending()
            binding.release()
            raise
        return True

    def validator(self, *, accepted=True):
        original = self.binding.admission
        confirmation_id = self.binding.facade._controller.pending_confirmation_id
        if original is None or not confirmation_id:
            return lambda: False

        def validate():
            binding = self.binding
            if (
                binding._closed
                or not binding._active
                or binding.context.session_id != original.session_id
                or not binding.facade._controller.accepts_confirmation(confirmation_id)
            ):
                return False
            selected_admission = None
            try:
                state = binding.service.state(binding.context)
                request = self._request(state, original.request_id)
                saved = state.get("confirmations", {}).get(original.request_id)
                if not saved or saved.get("confirmation_id") != confirmation_id:
                    return False
                self._require_current(request, original)
                if saved.get("lease_epoch") != request.lease_epoch:
                    return False
                if binding.admission is not None:
                    if binding.admission.request_id != original.request_id:
                        return False
                    selected_admission = binding.admission
                    binding.service.scheduler.validate(selected_admission)
                    self._require_current(request, selected_admission)
                    if set(selected_admission.ready_item_ids) != set(saved["item_ids"]):
                        raise RequestError("TURN_LEASE_STALE", "确认事项与当前轮次不一致。")
                else:
                    selected = self._select(request, saved)
                    if selected is None:
                        return False
                    selected_admission = selected.admission
                if selected_admission.epoch != original.epoch:
                    raise RequestError("TURN_LEASE_STALE", "确认所属视图的执行权限已经变化。")
                self._consume(saved, selected_admission, accepted=accepted)
                binding.admission = selected_admission
                if accepted:
                    binding._set_gate()
                    if saved["parsed"].get("mode") != "plan":
                        binding.gate.prepare_steps(saved["parsed"]["steps"])
                else:
                    QTimer.singleShot(0, lambda: self._finish_ignored(selected_admission))
                return True
            except Exception as exc:
                if selected_admission is not None:
                    binding.service.scheduler.release(selected_admission)
                    if binding.admission == selected_admission:
                        binding.release(interrupt=False)
                logger.exception("Request confirmation was not accepted")
                binding.facade.add_system_message(f"确认未生效，请重新查看请求：{exc}")
                return False

        return validate

    def _consume(self, saved, admission, *, accepted):
        binding = self.binding

        def consume(state):
            binding.service.scheduler.validate(admission)
            current = self._request(state, admission.request_id)
            self._require_current(current, admission)
            if state.get("confirmations", {}).get(admission.request_id) != saved:
                raise RequestError("CONFIRMATION_STALE", "确认已被处理或替换。")
            if self._candidate(current, saved) is None:
                raise RequestError("REQUEST_NOT_READY", "事项仍在等待，不能批准旧操作。")
            updated = current
            for reason in _APPROVAL_REASONS:
                updated = self._reduce(updated, "unblock", item_ids=saved["item_ids"], reason=reason)
            if not accepted:
                updated = self._reduce(updated, "pause", reason="user_paused")
            self._replace_request(state, updated)
            del state["confirmations"][admission.request_id]

        binding.service.transact(
            binding.context,
            consume,
            cause=EventCause(
                "confirmation.approved" if accepted else "confirmation.ignored",
                "user",
                {"request_ids": [admission.request_id], "item_ids": saved["item_ids"], "turn_id": admission.turn_id},
            ),
        )

    def _select(self, request, saved):
        candidate = self._candidate(request, saved)
        if candidate is None:
            return None
        tools = build_native_tool_definitions(
            self.binding.facade._conversation.get_loaded_tool_namespaces(), request_stage="execution"
        )
        selected = self.binding.service.scheduler.select_next_turn(
            request.session_id,
            self.binding.view_id,
            (candidate,),
            allowed_tools=tuple(tool.name for tool in tools),
            automatic=False,
        )
        if selected and (
            selected.admission.request_id != request.request_id
            or set(selected.admission.ready_item_ids) != set(saved["item_ids"])
        ):
            self.binding.service.scheduler.release(selected.admission)
            return None
        return selected

    @staticmethod
    def _candidate(request, saved):
        ids = saved.get("item_ids", ())
        if (
            request.terminal
            or request.pause_reasons
            or saved.get("revision") != request.revision
            or not isinstance(ids, (list, tuple))
            or not ids
            or any(not isinstance(i, str) for i in ids)
            or len(set(ids)) != len(ids)
            or not set(ids) <= {i.item_id for i in request.items}
        ):
            return None
        for item in request.items:
            if item.item_id in ids and (
                item.status != ItemStatus.WAITING
                or not set(item.waiting_reasons) & _APPROVAL_REASONS
                or set(item.waiting_reasons) - _APPROVAL_REASONS
            ):
                return None
        # This temporary scheduler projection never grants persistent approval.
        return replace(
            request,
            items=tuple(
                replace(item, status=ItemStatus.PENDING, waiting_reasons=())
                if item.item_id in ids
                else replace(item, waiting_reasons=(*item.waiting_reasons, "confirmation_scope"))
                for item in request.items
            ),
        )

    def _repropose_legacy(self, request, saved):
        def discard(state):
            current = self._request(state, request.request_id)
            if state.get("confirmations", {}).get(request.request_id) != saved or current.revision != request.revision:
                raise RequestError("CONFIRMATION_STALE", "旧确认已经变化，请重新查看请求。")
            # Original ownership is never inferred from legacy tool arguments.
            # Clear obsolete approval waits only to ask for a fresh proposal.
            for reason in _APPROVAL_REASONS:
                ids = [item.item_id for item in current.items if reason in item.waiting_reasons]
                if ids:
                    current = self._reduce(current, "unblock", item_ids=ids, reason=reason)
            self._replace_request(state, current)
            del state["confirmations"][request.request_id]

        self.binding.service.transact(
            self.binding.context,
            discard,
            cause=EventCause("confirmation.expired", "user", {"request_ids": [request.request_id]}),
        )
        self.binding.facade.add_system_message("旧确认的归属或执行权限已失效；将重新检查并提出待确认操作。")
        self.binding._priority = (request.request_id,)
        self.binding.wake()

    def _finish_ignored(self, admission):
        if self.binding.admission == admission:
            self.binding.release(interrupt=False)
            self.binding.wake()

    @staticmethod
    def _require_current(request, admission):
        if (
            request.request_id != admission.request_id
            or request.session_id != admission.session_id
            or request.revision != admission.request_revision
            or request.lease_epoch != admission.request_lease_epoch
            or request.scope != admission.scope
            or request.terminal
            or request.pause_reasons
            or admission.stage != "execution"
        ):
            raise RequestError("TURN_LEASE_STALE", "确认所属请求或执行权限已经变化。")

    def _request(self, state, request_id):
        request = next((r for r in self.binding.service.requests(state) if r.request_id == request_id), None)
        if request is None:
            raise RequestError("CONFIRMATION_STALE", "原请求已不存在。")
        return request

    @staticmethod
    def _replace_request(state, request):
        state["requests"] = [
            request.to_dict() if r["request_id"] == request.request_id else r for r in state["requests"]
        ]

    @staticmethod
    def _reduce(request, kind, **payload):
        return reduce_request(request, RequestEvent(uuid4().hex, kind, request.revision, payload))

    def _approval_items(self, admission):
        request = self._request(self.binding.service.state(self.binding.context), admission.request_id)
        execution = [
            i.item_id for i in request.items if i.kind == "execution" and i.item_id in admission.ready_item_ids
        ]
        return execution or list(admission.ready_item_ids)
