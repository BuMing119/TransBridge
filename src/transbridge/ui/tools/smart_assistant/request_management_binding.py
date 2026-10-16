"""Explicit request management intents from the request list."""

from uuid import uuid4

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from transbridge.application.assistant_requests.attachments import display_user_content
from transbridge.application.assistant_requests.journal import EventCause

from .message_bubble import MessageBubble
from .request_view_refresh import RequestViewRefresh
from .submission_scope import scope_from_input


class RequestManagementBinding:
    def __init__(self, binding):
        self.binding = binding
        self._timelines = set()
        self._refresh = RequestViewRefresh(binding)
        self._failed_turn = None
        self._operations = set()
        self._restart_commands = {}

    def capture_failed_turn(self):
        binding = self.binding
        if binding.admission is not None:
            self._failed_turn = (binding.context, binding.admission.request_id)

    def retry_failed_turn(self):
        binding = self.binding
        target = self._failed_turn
        if (
            target is None
            or binding._closed
            or not binding._active
            or binding.context != target[0]
            or binding.admission is not None
        ):
            return
        self._failed_turn = None
        request_id = target[1]
        if not request_id:
            self.retry_inputs()
            return
        try:
            request = next(
                (
                    r
                    for r in binding.service.requests(binding.service.state(binding.context))
                    if r.request_id == request_id
                ),
                None,
            )
            # A stale retry button must never create a successor for a completed request.
            if request is not None and not request.terminal and request.pause_reasons:
                binding.control(request_id, "resume")
        except Exception as exc:
            binding.fail(str(exc))

    def show_timeline(self, request_id):
        from transbridge.application.assistant_requests.journal import read_events

        from .request_timeline_view import RequestTimelineView

        binding = self.binding
        context = binding.context

        def load_page(sequence):
            return read_events(
                binding.service.state(context), request_id=request_id or None, after_sequence=sequence, limit=100
            )

        dialog = RequestTimelineView(load_page, request_only=bool(request_id), parent=binding.facade)
        self._timelines.add(dialog)
        dialog.finished.connect(lambda: self._timelines.discard(dialog))
        dialog.show()

    def stop_generation(self):
        binding = self.binding
        binding._user_stopped = True
        context, admission, batch = binding.context, binding.admission, binding.batch
        binding.interrupt()

        def stop():
            if admission is not None and admission.request_id:
                # Answer commit may win before cancellation. Terminal requests
                # retain their success; stopping only blocks further model work.
                request = next(
                    r
                    for r in binding.service.requests(binding.service.state(context))
                    if r.request_id == admission.request_id
                )
                if not request.terminal:
                    binding.service.command(context, admission.request_id, "interrupt", admission.request_revision)
            elif batch is not None:
                binding.service.transact(
                    context,
                    lambda state: [
                        entry.update(status="user_paused")
                        for entry in state.get("batches", ())
                        if entry["batch"]["batch_id"] == batch.batch_id
                    ],
                    cause=EventCause("routing.paused", "user"),
                )

        def stopped(_):
            if admission is not None and admission.request_id:
                binding.undo.stopped(admission.request_id)
            binding.refresh()

        binding.background.submit(stop, stopped, context=context)

    def retry_inputs(self):
        binding = self.binding
        binding._user_stopped = False
        context = binding.context

        def retry():
            binding.service.transact(
                context,
                lambda state: [
                    entry.update(status="routing")
                    for entry in state.get("batches", ())
                    if entry.get("status") == "user_paused"
                ],
                cause=EventCause("routing.resumed", "user"),
            )

        binding.background.submit(retry, context=context, wake=True)

    def refresh(self):
        self._refresh.request()

    def sync_inputs(self):
        binding = self.binding
        known = {m["message_id"] for m in binding.facade._conversation.get_transcript()}
        state = binding.service.state(binding.context)
        hidden = set(state.get("conversation_clear", {}).get("message_ids", ()))
        for entry in state.get("ingress", ()):
            if entry["message_id"] not in known | hidden:
                content = binding.service.input_content(binding.context, entry["message_id"])
                binding.facade._conversation.add_user(content, message_id=entry["message_id"])
                bubble = MessageBubble(display_user_content(content), "user", theme=binding.facade._theme)
                bubble.setProperty("message_id", entry["message_id"])
                bubble.set_scope(scope_from_input(entry))
                binding.facade._message_list.add_bubble(bubble)
        self.refresh()
        binding.wake()

    def restart(self, request, *, confirm=True):
        binding = self.binding
        context = binding.context
        key = (context, request.request_id, request.revision)
        eligible = request.status in {"failed", "cancelled"} if confirm else request.terminal
        if key in self._operations or not eligible or request.unsettled:
            return
        answer = (
            QMessageBox.question(
                binding.facade,
                "重新处理全部要求",
                f"将为“{request.goal}”创建关联的新任务，重新处理全部 {len(request.items)} 项要求，"
                "包括之前已完成的事项；可能再次产生修改。原任务及结果会保留。\n是否重新处理？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if confirm
            else QMessageBox.StandardButton.Yes
        )
        if answer != QMessageBox.StandardButton.Yes or binding.context != context or binding._closed:
            return
        self._operations.add(key)
        command_id = self._restart_commands.setdefault(key, uuid4().hex)
        selection = binding._selection()
        binding._user_stopped = False

        def work():
            return binding.service.restart(
                context,
                request.request_id,
                text=f"重新处理全部要求（包括已完成事项），保留原结果：{request.goal}",
                selection=selection,
                command_id=command_id,
            )

        def restarted(fresh):
            binding._priority = (fresh.request_id,)
            self.sync_inputs()

        binding.background.submit(work, restarted, context=context, settled=lambda: self._operations.discard(key))

    def retry_context(self, request_id):
        from transbridge.application.assistant_context.admission import clear_wait

        binding = self.binding
        context = binding.context
        key = (context, "context", request_id)
        if key in self._operations:
            return
        self._operations.add(key)
        binding._user_stopped = False
        binding.background.submit(
            lambda: clear_wait(binding.service, context, request_id),
            lambda _: binding.refresh(),
            context=context,
            wake=True,
            settled=lambda: self._operations.discard(key),
        )

    def clarify(self):
        from .routing_recovery_binding import RoutingRecoveryBinding

        if not hasattr(self, "_routing_recovery"):
            self._routing_recovery = RoutingRecoveryBinding(self.binding)
        self._routing_recovery.show()

    def reassign(self, request_id):
        binding = self.binding
        requests = binding.service.requests(binding.service.state(binding.context))
        source = next(r for r in requests if r.request_id == request_id)
        targets = [r for r in requests if r.request_id != request_id and not r.terminal]
        if not targets:
            binding.facade.add_system_message("没有其他可接收事项的未结束请求。")
            return
        labels = [f"{index + 1}. {item.description}" for index, item in enumerate(source.items)]
        label, ok = QInputDialog.getItem(binding.facade, "移动未执行事项", "选择未执行的事项", labels, editable=False)
        if not ok:
            return
        names = [f"{index + 1}. {r.goal}" for index, r in enumerate(targets)]
        name, ok = QInputDialog.getItem(binding.facade, "移动未执行事项", "移到哪个任务？", names, editable=False)
        if not ok:
            return
        target = targets[names.index(name)]
        binding.interrupt()
        try:
            binding.service.reassign(
                binding.context,
                request_id,
                target.request_id,
                source.items[labels.index(label)].item_id,
                expected_source_revision=source.revision,
                expected_target_revision=target.revision,
                text=f"将事项 {label} 从 {source.goal} 调整到 {target.goal}",
                selection=binding._selection(),
            )
            self.sync_inputs()
        except Exception as exc:
            binding.fail(str(exc))
