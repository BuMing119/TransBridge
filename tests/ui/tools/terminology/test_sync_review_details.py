from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.application.terminology_sync import test_draft_import as inbound
from tests.application.terminology_sync.test_service import _plan
from tests.application.terminology_sync.test_task_failure_feedback import runtime_service
from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import JobCapabilities, JobSpec, JobState, OwnerRef
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemOutcome,
    TerminologySyncItemStatus,
    TerminologySyncRetryToken,
)
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.plan_models import (
    TerminologyContentSummary,
    TerminologySyncAction,
    TerminologySyncPlanItem,
    TerminologySyncReason,
)
from transbridge.application.terminology_sync.use_case import (
    AuthorizedTerminologySyncPlan,
    TerminologySyncPlanStaleError,
)
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter, TerminologySyncViewState
from transbridge.ui.tools.terminology.sync_task_adapter import TerminologySyncTaskAdapter
from transbridge.ui.tools.terminology.sync_view import TerminologySyncPanel

_APP = QApplication.instance() or QApplication([])


def _item(index, action=TerminologySyncAction.UPDATE_REMOTE):
    return TerminologySyncPlanItem(
        f"item-{index:04}",
        action,
        TerminologySyncReason.LOCAL_CHANGED,
        remote_id=index + 1,
        local=TerminologyContentSummary(f"Sword {index}", f"sword {index}", "新译名", "project"),
        remote=TerminologyContentSummary(f"Sword {index}", f"sword {index}", "旧译名", "project"),
    )


def _panel(service, context, owner):
    presenter = TerminologySyncPresenter(service, context, owner)
    panel = TerminologySyncPanel(presenter)
    panel._run = lambda pending, call: panel._completed(call())
    return panel


def test_all_plan_pages_expose_target_chinese_actions_and_delete_impact():
    items = tuple(_item(i) for i in range(300)) + (_item(300, TerminologySyncAction.DELETE_REMOTE),)
    plan = replace(_plan(), items=items, plan_hash="")
    runtime, owner, context, _, service, _ = runtime_service(SimpleNamespace(), plan)
    panel = _panel(service, context, owner)
    try:
        panel.presenter.preflight(TerminologySyncMode.BACKUP)
        panel.render_sync(panel.presenter.create_plan())
        assert "https://paratranz.cn" in panel.target.text()
        assert "远端项目：123" in panel.target.text()
        assert "删除 1 项" in panel.confirmation.text()
        assert "不可" in panel.confirmation.text()
        assert not panel.execute_button.isEnabled()
        detail = panel.plan_details
        assert detail.range_label.text() == "1-100 / 301"
        assert "更新远端术语" == detail.table.item(0, 0).text()
        assert "旧译名" in detail.detail.toPlainText()
        assert "新译名" in detail.detail.toPlainText()
        for _ in range(3):
            detail.next_button.click()
        assert detail.range_label.text() == "301-301 / 301"
        assert detail.table.item(0, 0).text() == "删除远端术语"
        assert "Sword 300" in detail.detail.toPlainText()
        assert "无法自动撤销" in detail.detail.toPlainText()
        assert not detail.next_button.isEnabled()
        for _ in range(3):
            detail.previous_button.click()
        assert detail.range_label.text() == "1-100 / 301"
        assert not detail.previous_button.isEnabled()
    finally:
        panel.dispose()
        runtime.close()


def test_partial_result_pages_keep_term_names_failure_reason_and_retry_recovery():
    items = tuple(_item(i) for i in range(51))
    outcomes = tuple(
        TerminologySyncItemOutcome(
            item.item_id,
            item.action,
            TerminologySyncItemStatus.SUCCEEDED if i < 50 else TerminologySyncItemStatus.FAILED,
            "OK" if i < 50 else "RATE_LIMIT",
            "已完成" if i < 50 else "Server limited requests; retry later",
        )
        for i, item in enumerate(items)
    )
    token = TerminologySyncRetryToken("line", "target", "hash", "owner", tuple(x.item_id for x in items[:50]), ())
    result = TerminologyBackupExecutionResult("run", "hash", outcomes, token)
    panel = _panel(SimpleNamespace(), RequestContext("owner"), OwnerRef("owner", "gui"))
    try:
        panel.render_sync(TerminologySyncViewState(result=result, result_items=items, job_state=JobState.FAILED))
        assert "同步部分完成" in panel.summary.text()
        assert "成功项不会重复执行" in panel.summary.text()
        assert panel.retry_button.isEnabled()
        panel.result_details.next_button.click()
        assert panel.result_details.range_label.text() == "51-51 / 51"
        assert panel.result_details.table.item(0, 0).text() == "Sword 50"
        assert "RATE_LIMIT" in panel.result_details.detail.toPlainText()
        assert "retry later" in panel.result_details.detail.toPlainText()
        panel.result_details.previous_button.click()
        assert panel.result_details.range_label.text() == "1-50 / 51"
    finally:
        panel.dispose()


def test_runtime_failure_without_result_supersedes_old_plan_and_late_submit_completion(monkeypatch):
    def fail(_request):
        raise TerminologySyncPlanStaleError("Remote snapshot changed")

    runtime, owner, context, _, service, plan = runtime_service(SimpleNamespace(execute=fail))
    monkeypatch.setattr(
        service,
        "execute",
        lambda *_args: service.submit(AuthorizedTerminologySyncPlan(plan, owner.owner_id, "TEST"), owner),
    )
    panel = _panel(service, context, owner)
    try:
        panel.presenter.preflight(TerminologySyncMode.BACKUP)
        panel.render_sync(panel.presenter.create_plan())
        assert panel.execute_button.isEnabled()
        ref = panel.presenter.execute()
        panel.render_sync(panel.presenter.complete_job(ref))
        assert "同步失败" in panel.summary.text()
        assert "Remote snapshot changed" in panel.summary.text()
        assert "STALE_TERMINOLOGY_SYNC_PLAN" in panel.summary.text()
        assert "重新生成计划" in panel.summary.text()
        assert not panel.execute_button.isEnabled()
        panel._completed(ref)
        assert "同步失败" in panel.summary.text()
        assert panel.backup_button.isEnabled()
    finally:
        panel.dispose()
        runtime.close()


def test_cancel_callback_targets_current_job_and_cancelled_state_disables_old_plan():
    runtime, owner, context, _, service, _ = runtime_service(SimpleNamespace())
    panel = _panel(service, context, owner)
    try:
        panel.presenter.preflight(TerminologySyncMode.BACKUP)
        panel.presenter.create_plan()
        ref = runtime.submit(
            JobSpec(
                "operation.terminology_sync.backup", "input", "hash", capabilities=JobCapabilities(supports_cancel=True)
            ),
            owner,
        ).ref
        calls = []

        def cancel(selected):
            calls.append(selected)
            return runtime.cancel(selected, owner)

        panel.set_cancel_handler(cancel)
        panel.render_sync(panel.presenter.update_job(runtime.get(ref, owner)))
        assert not panel.cancel_button.isHidden()
        panel.cancel_button.click()
        assert calls == [ref]
        assert "同步已停止" in panel.summary.text()
        assert panel.cancel_button.isHidden()
        assert not panel.execute_button.isEnabled()
        assert not panel.presenter.observe_activity(
            JobRef("old", owner.owner_id, "old-run"), JobState.FAILED, terminal=True
        )
    finally:
        panel.dispose()
        runtime.close()


def test_real_inbound_conflict_preview_is_actionable_and_edit_invalidates_it():
    change_set = inbound._change_set(*(["add"] * 50), "conflict")
    importer, _, transactions, _ = inbound._service(change_set, effective=(inbound._decision("Sword", "剑"),))
    service = SimpleNamespace(
        list_inbound=lambda _context: (change_set,),
        prepare_import_selection=lambda _context, _set, choices: inbound._selection(change_set, choices),
        preview_import=importer.preview,
        commit_import=importer.commit,
    )
    context = inbound._context()
    panel = _panel(service, context, OwnerRef(context.owner_id, "gui", context.project_id, context.variant_id))
    committed = []
    panel.draft_committed.connect(committed.append)
    try:
        panel.render_sync(panel.presenter.load_inbound())
        panel.next_inbound_button.click()
        panel.previous_inbound_button.click()
        panel.preview_inbound_button.click()
        review = panel.inbound_review
        assert not panel.commit_inbound_button.isEnabled()
        assert "阻断" in review.notice.text()
        assert "请选择“编辑”" in review.diagnostics.toPlainText()
        assert review.preview_details.range_label.text() == "1-50 / 51"
        review.preview_details.next_button.click()
        assert review.preview_details.table.item(0, 0).text() == "阻断，需处理"
        review.preview_details.locate_button.click()
        assert panel.inbound_page_label.text() == "51-51 / 51"
        choice = panel.inbound_table.cellWidget(0, 2)
        choice.setCurrentIndex(choice.findData("edit"))
        panel.inbound_table.cellWidget(0, 3).setText("人工确认长剑")
        assert panel.presenter.state.inbound_proposal is None
        with pytest.raises(RuntimeError, match="preview"):
            panel.presenter.commit_inbound()
        panel.preview_inbound_button.click()
        assert panel.commit_inbound_button.isEnabled()
        review.preview_details.next_button.click()
        assert "人工确认长剑" in review.preview_details.detail.toPlainText()
        panel.commit_inbound_button.click()
        assert len(committed) == 1
        assert committed[0] == transactions.value.ref
        assert "待发布草稿" in review.notice.text()
        panel.render_sync(panel.presenter.state)
        assert len(committed) == 1
    finally:
        panel.dispose()


def test_reopening_sync_view_seeds_latest_task_instead_of_locking_to_first_history_item():
    runtime, owner, context, _, service, _ = runtime_service(SimpleNamespace())
    panel = _panel(service, context, owner)
    adapter = TerminologySyncTaskAdapter(runtime, owner, panel.render_activity)
    specification = JobSpec("operation.terminology_sync.backup", "input", "hash")
    try:
        old = runtime.submit(specification, owner).ref
        latest = runtime.submit(specification, owner).ref
        adapter.start()
        assert panel.presenter.state.job_ref == latest
        runtime.start(old, owner)
        assert panel.presenter.state.job_ref == latest
    finally:
        adapter.close()
        panel.dispose()
        runtime.close()
