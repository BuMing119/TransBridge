"""Explicit, evidenced user reconciliation for interrupted operation results."""

from uuid import uuid4

from PyQt6.QtWidgets import QInputDialog

from transbridge.application.assistant_requests.reconciliation import RequestReconciliationCoordinator
from transbridge.smart_assistant.tools.task_manager import TaskManager


def reconcile_outcome(binding, request_id):
    requests = binding.service.requests(binding.service.state(binding.context))
    request = next(r for r in requests if r.request_id == request_id)
    records = [(False, e) for e in request.effects if e.status == "outcome_unknown"]
    if not records:
        records = [(True, d) for d in request.dispatches if d.status == "outcome_unknown"]
    if not records:
        binding.facade.add_system_message("这个请求没有待核对的未知结果。")
        return
    items = {i.item_id: i.description for i in request.items}
    labels = [
        f"{index + 1}. {', '.join(items.get(i, i) for i in record.execution.item_ids)}"
        for index, (_, record) in enumerate(records)
    ]
    label, ok = QInputDialog.getItem(
        binding.facade, "核对执行结果", "选择已停止、结果未知的操作", labels, editable=False
    )
    if not ok:
        return
    is_dispatch, record = records[labels.index(label)]
    outcomes = {
        "已核实全部成功": "completed" if is_dispatch else "succeeded",
        "已核实失败": "failed",
        "已核实取消且没有提交": "cancelled",
    }
    outcome, ok = QInputDialog.getItem(binding.facade, "核对执行结果", "已核实的结果", list(outcomes), editable=False)
    if not ok:
        return
    reference, ok = QInputDialog.getMultiLineText(binding.facade, "核对依据", "填写检查过的文件、结果或回执及核对说明")
    if not ok or not reference.strip():
        return
    binding.interrupt()
    try:
        RequestReconciliationCoordinator(binding.service, TaskManager().runtime).resolve_user(
            binding.context,
            request_id,
            record.dispatch_id if is_dispatch else record.effect_id,
            is_dispatch=is_dispatch,
            expected_revision=request.revision,
            status=outcomes[outcome],
            reference=reference.strip(),
            command_id=uuid4().hex,
        )
        binding.management.sync_inputs()
    except Exception as exc:
        binding.fail(str(exc))
