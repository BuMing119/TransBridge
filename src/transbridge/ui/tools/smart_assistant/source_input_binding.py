"""Repair one captured assistant operation and present a freshly admitted decision."""

from copy import deepcopy
import logging

from PyQt6 import sip
from PyQt6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.io.identity import EntryKey
from transbridge.application.preflight.inputs import SourceInputFacts, missing_source_keys
from transbridge.smart_assistant.native_tools import build_native_tool_definitions
from transbridge.ui.source_input_state import original_known

from .request_confirmation import renew_confirmation
from .session_execution_access import ensure_execution

logger = logging.getLogger(__name__)


class AssistantSourceInputBinding:
    def __init__(self, facade):
        self.facade = facade
        self._closed = False
        self._seen = set()

    def close(self):
        self._closed = True

    def present(self, step, result):
        data = result.data if isinstance(result.data, dict) else {}
        intent = data.get("source_input_repair")
        if self._closed or result.success or not isinstance(intent, dict) or intent.get("kind") != "source_input":
            return
        identity = (intent.get("request_id"), step.get("tool_call_id"), id(step))
        if identity in self._seen:
            return
        self._seen.add(identity)
        intent, step = deepcopy(intent), deepcopy(step)
        binding = getattr(self.facade, "_request_binding", None)
        context = None if binding is None else binding.context
        card = QFrame(self.facade)
        layout = QVBoxLayout(card)
        slots = self.facade._ctx.slots
        names = [slots[key].label for key in intent.get("missing_source_keys", ()) if key in slots]
        missing_label = "、".join(names) if names else "对应来源输入"
        label = QLabel(f"本次操作需要补充：{missing_label}。查看清单后可批量补充，再确认执行范围。", card)
        label.setWordWrap(True)
        layout.addWidget(label)
        button = QPushButton("补充输入并重新准备", card)
        button.setAccessibleName("补充此助手操作需要的来源输入")
        layout.addWidget(button)
        button.clicked.connect(lambda: self._start(intent, step, context, label, button))
        self.facade._message_list.add_widget(card)

    def _current(self, intent, context, *, active=True):
        binding = getattr(self.facade, "_request_binding", None)
        identity = (intent.get("project_id"), intent.get("variant_id"))
        if self._closed or getattr(self.facade, "_shutdown_complete", False):
            return False
        if self.facade._ctx.active_version_identity != identity:
            return False
        return binding is None or (
            not binding._closed and binding.context == context and (binding._active or not active)
        )

    def _start(self, intent, step, context, label, button):
        if not ensure_execution(self.facade, "补充操作输入", lambda: self._start(intent, step, context, label, button)):
            return
        if not self._current(intent, context):
            label.setText("原操作的会话或工程版本已变化。请回到原会话后再补充。")
            return
        binding = getattr(self.facade, "_request_binding", None)
        if binding is not None and binding.admission is not None:
            if binding.admission.request_id != intent.get("request_id"):
                label.setText("正在处理另一项请求。该请求结束后可在这里补充输入。")
                return
            binding.release()
        button.setEnabled(False)
        self.facade._controller.handle_round_interrupted()

        def failed(exc):
            logger.warning("Assistant source input could not be resumed", exc_info=exc)
            if sip.isdeleted(label) or not self._current(intent, context):
                return
            label.setText(f"本次操作尚未继续：{exc}")
            button.setEnabled(True)

        def validated(_request=None):
            if not self._current(intent, context):
                return
            self._ensure(intent, step, context, label, button, failed)

        if binding is None or not intent.get("request_id") or intent.get("_confirmation_retry"):
            validated()
        else:
            binding.background.submit(lambda: self._waiting_request(binding, intent, context), validated, failed=failed)

    @staticmethod
    def _waiting_request(binding, intent, context):
        request = next(
            (
                request
                for request in binding.service.requests(binding.service.state(context))
                if request.request_id == intent["request_id"]
            ),
            None,
        )
        ids = set(intent["item_ids"])
        if (
            request is None
            or request.terminal
            or request.pause_reasons
            or request.session_id != intent["session_id"]
            or request.revision != intent["request_revision"]
            or request.lease_epoch != intent["request_lease_epoch"]
            or not ids <= {item.item_id for item in request.items}
            or any(
                item.item_id in ids and set(item.waiting_reasons) != {"source_input_required"} for item in request.items
            )
        ):
            raise RequestError("SOURCE_INPUT_REPAIR_STALE", "原事项已变化或另有等待条件，请核对原请求。")
        return request

    def _ensure(self, intent, step, context, label, button, failed):
        coordinator = self.facade._ctx.source_input_coordinator
        originals = tuple(EntryKey.from_dict(key) for key in intent["entry_keys"])
        mapped = coordinator.resolved_entry_keys(originals)
        # A second attempt can supply rows that the previous preview left unmapped.
        targets = set(mapped or originals)
        keys = tuple(
            key
            for key, slot in self.facade._ctx.slots.items()
            if any(entry.identity in targets for entry in slot.collection)
        )
        if intent["requirement"] == "template":
            selected = intent.get("_source_scope", {}).get("selected_source_keys", intent["source_keys"])
            keys = coordinator.resolved_keys(selected)

        def resume():
            if self._current(intent, context, active=False) and ensure_execution(self.facade, "重新准备原操作", resume):
                self._ensure(intent, step, context, label, button, failed)

        def finished(success):
            if not success and not sip.isdeleted(label) and self._current(intent, context):
                label.setText("本次补充未继续。工程和译文仍可编辑，可再次点击继续。")
                button.setEnabled(True)

        facts = (
            SourceInputFacts(
                key,
                all(
                    original_known(entry)
                    for entry in self.facade._ctx.slots[key].collection
                    if entry.identity in targets
                ),
                self.facade._ctx.slots[key].source_snapshot is not None,
            )
            for key in keys
            if key in self.facade._ctx.slots
        )
        if missing_source_keys(facts, intent["requirement"]):

            def selected_scope(selected):
                # Store the human decision in original request identities as well as
                # current slot identities; the application persists both on re-admission.
                chosen = [key for key in intent["source_keys"] if coordinator.resolved_keys((key,))[0] in selected]
                intent["_source_scope"] = {
                    "requested_source_keys": list(intent["source_keys"]),
                    "selected_source_keys": chosen,
                    "resolved_source_keys": list(coordinator.resolved_keys(chosen)),
                }
                resume()

            template = intent["requirement"] == "template"
            args = step.get("args", {})
            destination = args.get("output_dir") or args.get("path")
            coordinator.ensure(
                intent["requirement"],
                keys,
                resume=resume,
                parent=self.facade,
                on_finished=finished,
                is_current=lambda: self._current(intent, context, active=False),
                on_scope_ready=selected_scope if template else None,
                allow_partial=template and len(keys) > 1,
                operation_label="AI 助手写回" + (f" → {destination}" if destination else ""),
            )
            return
        if not mapped and intent["requirement"] != "template":
            label.setText("尚未对应本次操作的任何词条；保存译文仍保留，可再次补充并对应。")
            button.setEnabled(True)
            return
        renewed = deepcopy(step)
        renewed.pop("tool_call_id", None)
        renewed.pop("_prepared_write_back", None)
        args = renewed.setdefault("args", {})
        if intent["requirement"] == "template":
            args["source_keys"] = list(keys)
        else:
            args["entry_ids"] = [key.serialize() for key in mapped]
        self._repropose(intent, renewed, context, coordinator.entry_mappings(), label, button, failed)

    def _repropose(self, intent, step, context, mappings, label, button, failed):
        binding = getattr(self.facade, "_request_binding", None)
        parsed = {"mode": "react", "steps": [step]}
        ownership = {"selection": None, "transferred": False}

        def display(selected=None):
            if not self._current(intent, context):
                return
            try:
                if selected is not None:
                    binding.admission = selected.admission
                    binding._set_gate()
                self.facade._confirmation_view.invalidate_pending()
                self.facade._controller.handle_round_interrupted()
                proposal = renew_confirmation(parsed, self.facade._conversation)
                self.facade._controller.restore_confirmation(proposal)
                if selected is not None:
                    existing = binding.service.state(context).get("confirmations", {}).get(intent["request_id"])
                    if existing is not None and existing.get("lease_epoch") == selected.admission.request_lease_epoch:
                        raise RequestError("CONFIRMATION_STALE", "已有当前许可的待确认操作，请先处理原确认。")
                    binding.confirmations.capture(proposal, expected_confirmation=existing)
                ownership["transferred"] = True
                intent.pop("_confirmation_retry", None)
                source_scope = intent.get("_source_scope", {})
                skipped = len(source_scope.get("requested_source_keys", ())) - len(
                    source_scope.get("selected_source_keys", ())
                )
                label.setText(
                    "输入准备已确认，已重新准备此操作。请在新卡片核对范围并确认；未对应译文保留。"
                    + (f" 本次跳过 {skipped} 个来源，其译文仍保留。" if skipped else "")
                )
                button.setEnabled(False)
            except Exception as exc:
                self.facade._confirmation_view.invalidate_pending()
                self.facade._controller.handle_round_interrupted()
                if selected is not None and binding.admission == selected.admission:
                    binding.admission = binding.gate = None
                failed(exc)

        if binding is None or not intent.get("request_id"):
            display()
            return
        tools = build_native_tool_definitions(
            self.facade._conversation.get_loaded_tool_namespaces(), request_stage="execution"
        )

        def prepare():
            from transbridge.application.assistant_requests.source_input_selection import (
                prepare_source_input_repair,
                readmit_source_input_confirmation,
            )

            values = (
                binding.service,
                context,
                intent["request_id"],
                intent["request_revision"],
                intent["request_lease_epoch"],
                tuple(intent["item_ids"]),
            )
            options = {"view_id": binding.view_id, "allowed_tools": tuple(tool.name for tool in tools)}
            if intent.get("_confirmation_retry"):
                selected = readmit_source_input_confirmation(*values, **options)
            else:
                self._waiting_request(binding, intent, context)
                selected = prepare_source_input_repair(
                    *values, mappings, source_scope=intent.get("_source_scope"), **options
                )
            ownership["selection"] = selected
            intent.update(
                request_revision=selected.request.revision,
                request_lease_epoch=selected.request.lease_epoch,
                _confirmation_retry=True,
            )
            return selected

        def settled():
            selected = ownership["selection"]
            if selected is not None and not ownership["transferred"]:
                binding.service.scheduler.release(selected.admission)

        binding.background.submit(prepare, display, failed=failed, settled=settled)
