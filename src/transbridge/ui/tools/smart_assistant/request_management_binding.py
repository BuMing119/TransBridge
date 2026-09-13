"""Explicit request management intents from the request list."""

from PyQt6.QtWidgets import QInputDialog

from transbridge.application.assistant_requests.journal import EventCause

from .message_bubble import MessageBubble
from .request_view_refresh import RequestViewRefresh


class RequestManagementBinding:
    def __init__(self, binding):
        self.binding = binding
        self._timelines = set()
        self._refresh = RequestViewRefresh(binding)
        self._failed_turn = None

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
        self.binding._user_stopped = True
        try:
            if self.binding.admission is not None and self.binding.admission.request_id:
                self.binding.service.command(
                    self.binding.context,
                    self.binding.admission.request_id,
                    "interrupt",
                    self.binding.admission.request_revision,
                )
            elif self.binding.batch is not None:
                batch_id = self.binding.batch.batch_id
                self.binding.service.transact(
                    self.binding.context,
                    lambda state: [
                        entry.update(status="user_paused")
                        for entry in state.get("batches", ())
                        if entry["batch"]["batch_id"] == batch_id
                    ],
                    cause=EventCause("routing.paused", "user"),
                )
            self.binding.interrupt()
        except Exception as exc:
            self.binding.fail(str(exc))

    def retry_inputs(self):
        self.binding._user_stopped = False
        try:
            self.binding.service.transact(
                self.binding.context,
                lambda state: [
                    entry.update(status="routing")
                    for entry in state.get("batches", ())
                    if entry.get("status") == "user_paused"
                ],
                cause=EventCause("routing.resumed", "user"),
            )
            self.binding.wake()
        except Exception as exc:
            self.binding.fail(str(exc))

    def refresh(self):
        self._refresh.request()

    def sync_inputs(self):
        binding = self.binding
        known = {m["message_id"] for m in binding.facade._conversation.get_transcript()}
        for entry in binding.service.state(binding.context).get("ingress", ()):
            if entry["message_id"] not in known:
                binding.facade._conversation.add_user(entry["text"], message_id=entry["message_id"])
                binding.facade._message_list.add_bubble(
                    MessageBubble(entry["text"], "user", theme=binding.facade._theme)
                )
        self.refresh()
        binding.wake()

    def restart(self, request):
        binding = self.binding
        fresh = binding.service.restart(
            binding.context,
            request.request_id,
            text=f"继续已结束的请求：{request.goal}",
            selection=binding._selection(),
        )
        binding._priority = (fresh.request_id,)
        self.sync_inputs()

    def clarify(self):
        binding = self.binding
        state = binding.service.state(binding.context)
        pending = [
            (entry["batch"], receipt)
            for entry in state.get("batches", ())
            for receipt in entry["batch"].get("receipts", ())
            if receipt["status"] == "needs_clarification"
        ]
        if not pending:
            return
        labels = []
        for index, (batch, receipt) in enumerate(pending):
            directive = next(d for d in batch["proposal"]["directives"] if d["local_id"] == receipt["local_id"])
            source = next(s for s in batch["sources"] if s["message_id"] == directive["message_id"])
            start, end = directive["span"]
            labels.append(f"{index + 1}. {source['text'][start:end]}")
        choice, ok = QInputDialog.getItem(binding.facade, "澄清请求归属", "选择需要澄清的指令", labels, editable=False)
        if not ok:
            return
        batch, receipt = pending[labels.index(choice)]
        requests = binding.service.requests(state)
        targets = [f"{index + 1}. {request.goal}" for index, request in enumerate(requests)]
        if not targets:
            binding.facade.add_system_message("还没有可关联的请求，请补充说明目标。")
            return
        target, ok = QInputDialog.getItem(
            binding.facade, "澄清请求归属", "这条指令针对哪个请求？", targets, editable=False
        )
        if not ok:
            return
        binding.interrupt()
        try:
            binding.service.clarify(
                binding.context,
                batch["batch_id"],
                receipt["local_id"],
                requests[targets.index(target)].request_id,
                text=f"将待澄清指令 {receipt['local_id']} 关联到请求：{target}",
                selection=binding._selection(),
            )
            self.sync_inputs()
        except Exception as exc:
            binding.fail(str(exc))

    def reassign(self, request_id):
        binding = self.binding
        requests = binding.service.requests(binding.service.state(binding.context))
        source = next(r for r in requests if r.request_id == request_id)
        targets = [r for r in requests if r.request_id != request_id and not r.terminal]
        if not targets:
            binding.facade.add_system_message("没有其他可接收事项的未结束请求。")
            return
        labels = [f"{index + 1}. {item.description}" for index, item in enumerate(source.items)]
        label, ok = QInputDialog.getItem(binding.facade, "调整事项归属", "选择未执行的事项", labels, editable=False)
        if not ok:
            return
        names = [f"{index + 1}. {r.goal}" for index, r in enumerate(targets)]
        name, ok = QInputDialog.getItem(binding.facade, "调整事项归属", "选择目标请求", names, editable=False)
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
