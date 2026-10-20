"""Present unresolved input choices without confusing schema errors with target ambiguity."""

from PyQt6.QtWidgets import QInputDialog, QMessageBox

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.assistant_requests.routing_recovery import (
    directive_source,
    resolve_routing_input,
    unresolved_directives,
)

from .session_execution_access import ensure_execution, execution_allowed


class RoutingRecoveryBinding:
    def __init__(self, binding):
        self.binding = binding
        self._busy = False

    def show(self):
        binding, context = self.binding, self.binding.context
        if self._busy:
            return
        if not ensure_execution(binding.facade, "处理待澄清要求", self.show):
            return
        self._busy = True

        def loaded(state):
            try:
                self._choose(state, context)
            finally:
                self._busy = False

        binding.background.submit(
            lambda: binding.service.state(context),
            loaded,
            context=context,
            settled=lambda: setattr(self, "_busy", False),
        )

    def _choose(self, state, context):
        binding = self.binding
        if binding.context != context or binding._closed or not execution_allowed(binding.facade):
            return
        pending = list(unresolved_directives(state))
        if not pending:
            return
        labels = [
            f"{index + 1}. {'选择任务' if kind == 'target' else '需要重新规划'} · "
            + directive_source(batch, receipt.local_id)[1]
            for index, (batch, receipt, kind) in enumerate(pending)
        ]
        selected, ok = QInputDialog.getItem(binding.facade, "处理未接纳的要求", "选择要求", labels, editable=False)
        if not ok or binding.context != context or binding._closed or not execution_allowed(binding.facade):
            return
        batch, receipt, kind = pending[labels.index(selected)]
        dialog = QMessageBox(binding.facade)
        dialog.setWindowTitle("要求尚未接纳")
        dialog.setText(
            "尚不能确定这条补充针对哪个任务。可以选择目标任务，或重新规划、放弃这条要求。"
            if kind == "target"
            else "模型的处理方案未通过验证。可以只为这条要求重新规划，或放弃它；已接纳的其他要求不会重放。"
        )
        dialog.setDetailedText(receipt.diagnostic)
        target = dialog.addButton("选择目标任务", QMessageBox.ButtonRole.ActionRole) if kind == "target" else None
        replan = dialog.addButton("重新规划", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton("放弃这条要求", QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if binding.context != context or binding._closed or not execution_allowed(binding.facade):
            return
        if target is not None and dialog.clickedButton() is target:
            return self._target(state, context, batch, receipt)
        action = (
            "replan" if dialog.clickedButton() is replan else "discard" if dialog.clickedButton() is discard else ""
        )
        if action:
            self._submit(
                context,
                lambda: resolve_routing_input(
                    binding.service, context, batch.batch_id, receipt.local_id, action=action
                ),
            )

    def _target(self, state, context, batch, receipt):
        binding = self.binding
        source, _ = directive_source(batch, receipt.local_id)
        requests = [
            r for r in binding.service.requests(state) if r.scope == source.scope and r.session_id == batch.session_id
        ]
        names = [f"{index + 1}. {r.goal}" for index, r in enumerate(requests)]
        if not names:
            binding.facade.add_system_message(
                "还没有同范围的任务可关联。请重新说明目标，或在待处理要求中放弃这条补充。"
            )
            return
        name, ok = QInputDialog.getItem(
            binding.facade, "选择补充要求所属的任务", "这条要求针对哪个任务？", names, editable=False
        )
        if not ok or binding.context != context or binding._closed or not execution_allowed(binding.facade):
            return
        target = requests[names.index(name)]
        selection = binding._selection()
        self._submit(
            context,
            lambda: binding.service.clarify(
                context,
                batch.batch_id,
                receipt.local_id,
                target.request_id,
                text=f"将待澄清要求关联到任务：{target.goal}",
                selection=selection,
            ),
        )

    def _submit(self, context, work):
        binding = self.binding
        if binding.context != context or binding._closed or not execution_allowed(binding.facade):
            return
        binding._user_stopped = False
        binding.interrupt()

        def apply():
            if binding.context != context or binding._closed or not execution_allowed(binding.facade):
                raise RequestError("SESSION_SCOPE_MISMATCH", "工程已切换，请回到会话所属工程后重试。")
            return work()

        binding.background.submit(apply, lambda _: binding.management.sync_inputs(), context=context)
