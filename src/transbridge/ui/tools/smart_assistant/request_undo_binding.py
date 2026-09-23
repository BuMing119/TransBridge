"""Offer explicit, optional round undo after background work has settled."""

from threading import Event

from PyQt6.QtWidgets import QMessageBox

from transbridge.application.assistant_requests.models import RequestError


class RequestUndoBinding:
    def __init__(self, binding):
        self.binding = binding
        self._target = None
        self._round_id = ""
        self._previewing = False
        self._previewed = False
        self._cancelled = Event()

    def stopped(self, request_id):
        binding = self.binding
        self._cancelled.set()
        self._cancelled = Event()
        self._target = (binding.context, binding._turn_generation, request_id)
        self._round_id = ""
        self._previewing = self._previewed = False
        binding.view.show_undo("正在停止当前请求，等待后台操作收尾。", available=False)
        binding.refresh()

    def invalidate(self):
        self._cancelled.set()
        self._target = None
        self._round_id = ""
        self._previewing = self._previewed = False
        self.binding.view.show_undo("", available=False)

    def _current(self, target):
        binding = self.binding
        return (
            target is not None
            and self._target == target
            and not binding._closed
            and binding.context == target[0]
            and binding._turn_generation == target[1]
        )

    def observe(self, requests):
        target = self._target
        if not self._current(target):
            if target is not None:
                self.invalidate()
            return
        request = next((request for request in requests if request.request_id == target[2]), None)
        if request is None or request.unsettled or (not request.terminal and not request.pause_reasons):
            return
        self._round_id = request.work_round_id
        if not self._round_id:
            self.binding.view.show_undo("本轮已停止。旧记录没有可核验的用户轮次归属，不能自动撤销。", available=False)
            return
        if not self._previewed and not self._previewing:
            self._preview(target, clicked=False)

    def request(self):
        target = self._target
        if self._current(target) and self._round_id and not self._previewing:
            self._preview(target, clicked=True)

    def _preview(self, target, *, clicked):
        binding, round_id = self.binding, self._round_id
        self._previewing = True
        binding.view.show_undo("正在核验本轮可撤销范围。", available=False)

        def preview():
            if binding.service.undo is None:
                return {"available": False, "reason": "当前会话未提供安全撤销服务"}
            return binding.service.undo.preview(target[0], round_id)

        def received(result):
            if not self._current(target):
                return
            self._previewing = False
            self._previewed = True
            if not result["available"]:
                binding.view.show_undo(
                    f"该请求已停止。{result.get('reason') or '没有可安全撤销的修改。'}", available=False
                )
                return
            binding.view.show_undo("本轮已停止。已完成的修改保留，可选择撤销。", available=True)
            if not clicked:
                return
            limitations = result.get("limitations", ())
            if limitations:
                details = "\n".join(f"• {item.get('tool', '操作')}：{item['reason']}" for item in limitations)
                accepted = (
                    QMessageBox.question(
                        binding.facade,
                        "只能撤销部分修改",
                        f"以下操作不能自动恢复：\n{details}\n\n是否仅撤销其余已核验的修改？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    == QMessageBox.StandardButton.Yes
                )
                if not accepted or not self._current(target):
                    return
            self._undo(target, round_id, allow_partial=bool(limitations))

        binding.background.submit(preview, received, failed=lambda exc: self._failed(target, exc), wake=True)

    def _undo(self, target, round_id, *, allow_partial):
        binding = self.binding
        cancelled = self._cancelled
        self._previewing = True
        binding.view.show_undo("正在撤销已核验的本轮修改。", available=False)

        def received(result):
            if not self._current(target):
                return
            records = result.get("records")
            if records is not None:
                binding.facade._conversation.merge_saved_records(records)
            self._previewing = False
            text = "本轮可恢复的修改已撤销；其余操作保持原状。" if result["partial"] else "本轮已记录的修改已撤销。"
            binding.view.show_undo(text, available=False)
            # The normal Project command publishes authoritative projection.
            # Do not restore a captured entry collection or replay a task graph.
            binding.refresh()

        def apply():
            if cancelled.is_set():
                raise RequestError("UNDO_VIEW_CHANGED", "视图已切换，尚未执行的撤销已取消")
            result = binding.service.undo.undo(target[0], round_id, allow_partial=allow_partial)
            from transbridge.persistence.v2.ids import SessionId, SessionRef

            snapshot = binding.service.lifecycle.read_session(SessionRef(SessionId(target[0].session_id)), target[0])
            return {**result, "records": snapshot.backend_messages()}

        binding.background.submit(apply, received, failed=lambda exc: self._failed(target, exc), wake=True)

    def _failed(self, target, exc):
        if self._current(target):
            self._previewing = False
            self._previewed = True
            self.binding.view.show_undo(f"撤销未完成：{exc}。请核对结果，不会自动重试。", available=False)
