from datetime import UTC, datetime
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import OwnerRef
from transbridge.application.terminology_sync.execution_models import (
    TerminologyBackupExecutionResult,
    TerminologySyncItemOutcome,
    TerminologySyncItemStatus,
    TerminologySyncRetryToken,
)
from transbridge.application.terminology_sync.models import TerminologySyncMode
from transbridge.application.terminology_sync.plan_models import TerminologyContentSummary, TerminologySyncAction
from transbridge.ui.tools.terminology.sync_presenter import TerminologySyncPresenter, TerminologySyncViewState
from transbridge.ui.tools.terminology.sync_view import SYNC_ACTIONS, TerminologySyncPanel

_APP = QApplication.instance() or QApplication([])


def test_sync_view_exposes_two_explicit_actions_and_safe_inbound_copy() -> None:
    assert [(action.mode, action.label) for action in SYNC_ACTIONS] == [
        (TerminologySyncMode.BACKUP, "备份已发布版本"),
        (TerminologySyncMode.BIDIRECTIONAL, "双向同步"),
    ]
    assert "不会自动发布" in SYNC_ACTIONS[1].description


class _ResultService:
    def __init__(self, result: TerminologyBackupExecutionResult) -> None:
        self.execution_result = result
        self.result_refs: list[JobRef] = []

    def status(self, ref: JobRef, actor: OwnerRef) -> object:
        del ref, actor
        return SimpleNamespace(is_terminal=True)

    def result(self, ref: JobRef, actor: OwnerRef) -> TerminologyBackupExecutionResult:
        del actor
        self.result_refs.append(ref)
        return self.execution_result


def _unknown_result() -> TerminologyBackupExecutionResult:
    token = TerminologySyncRetryToken("line", "target", "hash", "owner", (), ("item",))
    return TerminologyBackupExecutionResult(
        "run",
        "hash",
        (
            TerminologySyncItemOutcome(
                "item",
                TerminologySyncAction.UPDATE_REMOTE,
                TerminologySyncItemStatus.UNKNOWN,
                "UNKNOWN",
                "remote outcome unknown",
            ),
        ),
        token,
        reconcile_required=True,
    )


def _presenter(service: _ResultService) -> TerminologySyncPresenter:
    return TerminologySyncPresenter(
        service,  # type: ignore[arg-type]
        RequestContext("owner", project_id="project", variant_id="variant"),
        OwnerRef("owner", "gui", "project", "variant"),
    )


def test_sync_panel_renders_unknown_result_with_reconcile_only() -> None:
    result = _unknown_result()
    panel = TerminologySyncPanel(_presenter(_ResultService(result)))

    panel.render_sync(TerminologySyncViewState(result=result))

    assert "未知远端状态" in panel.summary.text()
    assert not panel.retry_button.isHidden()
    assert not panel.retry_button.isEnabled()
    assert not panel.reconcile_button.isHidden()
    assert panel.reconcile_button.isEnabled()


def test_terminal_activity_requests_authoritative_result_off_the_render_callback() -> None:
    result = _unknown_result()
    service = _ResultService(result)
    panel = TerminologySyncPanel(_presenter(service))
    completed: list[tuple[str, object]] = []
    panel._run = lambda pending, call: completed.append((pending, call()))  # type: ignore[method-assign]  # noqa: SLF001
    activity = SimpleNamespace(
        display_context=SimpleNamespace(title="terminology sync"),
        state=SimpleNamespace(value="completed"),
        is_terminal=True,
        job_id="job-1",
        run_id="run",
        owner=SimpleNamespace(owner_id="owner"),
    )

    panel.render_activity(activity)  # type: ignore[arg-type]

    ref = JobRef("job-1", "owner", "run")
    assert service.result_refs == [ref]
    assert completed[0][0] == "result"
    assert completed[0][1].result is result


def test_sync_panel_exposes_paged_accept_reject_edit_preview_and_draft_commit_controls() -> None:
    content = TerminologyContentSummary("Dragon", "dragon", "龙", "global")
    first = SimpleNamespace(item_id="item-1", remote=content, local=None)
    second = SimpleNamespace(item_id="item-2", remote=content, local=None)
    change_set = SimpleNamespace(
        change_set_id="set-1",
        created_at=datetime(2026, 8, 30, tzinfo=UTC),
        items=(first, second),
    )
    proposal = SimpleNamespace(committable=True)
    panel = TerminologySyncPanel(_presenter(_ResultService(_unknown_result())))

    panel.render_sync(
        TerminologySyncViewState(
            inbound_sets=(change_set,),  # type: ignore[arg-type]
            selected_inbound_id="set-1",
            inbound_items=(first,),  # type: ignore[arg-type]
            inbound_offset=0,
            inbound_total=2,
            inbound_proposal=proposal,  # type: ignore[arg-type]
        )
    )

    assert not panel.inbound_section.isHidden()
    assert panel.inbound_selector.count() == 1
    assert panel.inbound_table.rowCount() == 1
    decision = panel.inbound_table.cellWidget(0, 2)
    edited = panel.inbound_table.cellWidget(0, 3)
    assert isinstance(decision, QComboBox)
    assert isinstance(edited, QLineEdit)
    assert [decision.itemData(index) for index in range(decision.count())] == ["accept", "reject", "edit"]
    assert edited.text() == "龙"
    assert panel.inbound_page_label.text() == "1-1 / 2"
    assert panel.next_inbound_button.isEnabled()
    assert panel.preview_inbound_button.isEnabled()
    assert panel.commit_inbound_button.isEnabled()
    assert "待发布" in panel.inbound_notice.text()
