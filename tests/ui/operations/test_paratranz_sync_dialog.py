from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
import os
from threading import Thread
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton
import pytest

from tests.ui.operations.test_operation_plan_coordinator_async import _DeferredWorker
from transbridge.application.contracts import (
    Diagnostic,
    DomainError,
    ErrorCategory,
    OperationCounts,
    OperationResult,
    RequestContext,
)
from transbridge.application.sync import SyncOperation
from transbridge.application.tasks import CallbackThreadBackend, JobSpec, JobState, OwnerRef, TaskRuntime
from transbridge.ui.operations.facade import OperationFeatureAdapter, OperationPlanFacade
from transbridge.ui.operations.mappers import (
    DownloadOperationMapper,
    FomodOperationMapper,
    OperationPlanDraft,
    UploadOperationMapper,
    WriteOperationMapper,
)
from transbridge.ui.operations.paratranz_dialog import ParaTranzSyncDialog
from transbridge.ui.operations.plan_view import (
    EditableControl,
    EditableFieldState,
    OperationKind,
    OperationPlanViewState,
)
from transbridge.ui.operations.preflight_view import OperationPreflightResult, PreflightCheckState, PreflightCheckStatus
from transbridge.ui.operations.sync_review_dialog import SyncReviewDialog
from transbridge.ui.operations.sync_review_projection import review_rows
from transbridge.ui.operations.sync_review_view import KEEP_LOCAL, KEEP_REMOTE, SyncReviewCandidate, SyncReviewRow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _plan() -> OperationPlanViewState:
    return OperationPlanViewState(
        "session",
        1,
        OperationKind.DOWNLOAD,
        "从 ParaTranz 更新本地翻译",
        "天际中文翻译 · 当前工程已绑定",
        "天际汉化 / 正式版 / 8,300 条翻译内容",
        "使用 ParaTranz 内容更新本地",
        "云端内容优先",
        "下载前自动创建历史还原点",
        (("local_entries", 8300),),
        editable_fields=(
            EditableFieldState(
                "paratranz_project_id",
                "云端项目",
                "18668",
                control=EditableControl.REMOTE_PROJECT,
                display_value="天际中文翻译",
            ),
            EditableFieldState("set_as_default", "以后默认使用这个云端项目", "false", enabled=False),
            EditableFieldState(
                "conflict_policy",
                "同步方式",
                "prefer_remote",
                control=EditableControl.CHOICE,
                options=(
                    ("prefer_remote", "使用 ParaTranz 内容更新本地（推荐）"),
                    ("prefer_local", "保留本地已有内容，只补充云端新增内容"),
                ),
            ),
            EditableFieldState(
                "apply_remote_deletions",
                "同步云端删除",
                "false",
                control=EditableControl.BOOLEAN,
            ),
        ),
        request_digest="d" * 64,
    )


def test_dialog_waits_for_explicit_comparison_after_project_and_mode_selection(qapp) -> None:
    context = SimpleNamespace(config=SimpleNamespace(config_revision=0), active_project_id="local")
    dialog = ParaTranzSyncDialog(_plan(), context)
    requested = []
    dialog.preflight_requested.connect(lambda session_id, values: requested.append((session_id, dict(values))))
    dialog.show()
    qapp.processEvents()

    assert dialog._target_name.text() == "天际中文翻译"
    assert "18668" not in dialog._target_name.text()
    assert not dialog.findChildren(QLineEdit)
    assert dialog._confirm.text() == "开始对照"
    assert not requested
    assert dialog._choose_project.isEnabled()
    assert dialog._strategy_buttons["prefer_local"].isEnabled()
    dialog._strategy_buttons["prefer_local"].click()
    qapp.processEvents()
    assert not requested
    dialog._confirm.click()
    assert requested and requested[-1][0] == "session"
    assert requested[-1][1] == {
        "paratranz_project_id": "18668",
        "paratranz_project_name": "天际中文翻译",
        "set_as_default": "false",
        "conflict_policy": "prefer_local",
        "apply_remote_deletions": "false",
        "download_decisions": "{}",
    }
    dialog.set_preflight_running(False)
    dialog.close()


def test_dialog_renders_plain_language_impact_and_enables_one_primary_action(qapp) -> None:
    dialog = ParaTranzSyncDialog(
        _plan(),
        SimpleNamespace(config=SimpleNamespace(config_revision=0), active_project_id="local"),
    )
    qapp.processEvents()
    dialog.render_preflight(
        OperationPreflightResult(
            OperationKind.DOWNLOAD,
            "d" * 64,
            "remote:18668:7",
            (),
            ("在本地聚合事务中应用远端更新",),
            object(),
            (("update_local", 126), ("create_local", 23), ("skip", 8017), ("delete_local", 0)),
        )
    )
    dialog.set_preflight_running(False)

    assert dialog._impact.text() == "更新 126  ·  新增 23  ·  保留 8,017"
    assert dialog._confirm.isEnabled()
    assert dialog._confirm.text() == "更新工程译文"
    assert dialog._status.text() == "已核对，可以更新工程译文；不会写回游戏文件。"
    assert "不会写回 ESM/ESP" in dialog._scope.text()
    dialog.close()


@pytest.fixture
def dialog(qapp):
    subject = ParaTranzSyncDialog(
        _plan(), SimpleNamespace(config=SimpleNamespace(config_revision=0), active_project_id="local")
    )
    subject.show()
    qapp.processEvents()
    yield subject
    subject.set_preflight_running(False)
    subject.set_execution_running(False)
    subject.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _ready(subject):
    subject.render_preflight(
        OperationPreflightResult(
            subject._plan.kind,
            subject._plan.request_digest,
            "remote:18668:7",
            (),
            (),
            object(),
            (("update_local", 1), ("update_remote", 1)),
        )
    )
    subject.set_preflight_running(False)


def test_executing_dialog_shows_progress_and_blocks_all_controls_and_close(dialog, qapp):
    _ready(dialog)
    dialog.set_execution_running(True)
    before = dialog.edited_values()
    requested = []
    dialog.confirm_requested.connect(lambda *_args: requested.append("confirm"))
    dialog.preflight_requested.connect(lambda *_args: requested.append("preflight"))

    assert dialog._progress.isVisible()
    assert (dialog._progress.minimum(), dialog._progress.maximum()) == (0, 0)
    for control in (*dialog.findChildren(QPushButton), *dialog._strategy_buttons.values(), dialog._apply_deletions):
        assert not control.isEnabled()
        control.click()
    assert not dialog._set_default.isEnabled()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.close()
    dialog.reject()
    dialog.accept()
    qapp.processEvents()

    assert dialog.isVisible()
    assert dialog.edited_values() == before
    assert requested == []


def test_changed_options_invalidate_confirmation_without_starting_network(dialog, qapp):
    _ready(dialog)
    assert not dialog._progress.isVisible()
    assert dialog._choose_project.isEnabled()
    assert dialog._apply_deletions.isEnabled()
    assert not dialog._set_default.isEnabled()
    assert dialog._cancel.isEnabled()
    requested, invalidated = [], []
    dialog.plan_input_changed.connect(lambda: invalidated.append(True))
    dialog.preflight_requested.connect(lambda _session, fields: requested.append(dict(fields)))
    dialog._strategy_buttons["prefer_local"].click()

    assert not dialog._progress.isVisible()
    assert dialog._confirm.isEnabled() and dialog._confirm.text() == "开始对照"
    assert dialog._cancel.isEnabled()
    assert dialog._preflight is None
    assert invalidated == [True]
    qapp.processEvents()
    assert requested == []
    dialog._confirm.click()
    assert len(requested) == 1
    assert requested[0]["conflict_policy"] == "prefer_local"


@pytest.mark.parametrize("blocked", [False, True], ids=["worker-error", "blocked-result"])
def test_failed_preflight_unlocks_and_can_be_rechecked(dialog, blocked):
    if blocked:
        dialog.render_preflight(
            OperationPreflightResult(
                OperationKind.DOWNLOAD,
                dialog._plan.request_digest,
                "remote:18668:7",
                (PreflightCheckState("network", "连接检查", PreflightCheckStatus.BLOCKED, "连接超时"),),
                (),
            )
        )
        dialog.set_preflight_running(False)
    else:
        dialog.render_preflight_error("连接超时")
    assert not dialog._progress.isVisible()
    assert dialog._cancel.isEnabled()
    assert dialog._choose_project.isEnabled()
    assert "连接超时" in dialog._status.text()
    assert dialog._confirm.text() == "重新对照"
    requested = []
    dialog.preflight_requested.connect(lambda *_args: requested.append(True))
    dialog._confirm.click()
    assert requested == [True]
    assert dialog._progress.isVisible()
    assert dialog._cancel.isEnabled()


@pytest.fixture
def sync_case(qapp):
    queued, workers, submitted = [], [], []
    tasks = TaskRuntime(
        id_generator=SimpleNamespace(new_id=lambda: f"sync-{len(submitted)}"),
        clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
        backend=CallbackThreadBackend(lambda _run_id, target: queued.append(target)),
    )

    def create(_context, _batch, _values):
        return OperationPlanDraft(
            request=object(),
            target="ParaTranz 项目",
            target_revision="remote:7",
            input_fingerprint="local:1",
            scope_summary="1 条翻译内容",
            mode_summary="同步",
            conflict_summary="云端内容优先",
            backup_summary="自动历史还原点",
            estimated_impact=(("update_local", 1), ("update_remote", 1)),
            editable_fields=_plan().editable_fields,
        )

    def submit(_draft, _preflight, owner, runtime):
        subject = case.dialog
        assert subject._progress.isVisible()
        assert not subject._cancel.isEnabled()
        if case.submit_error is not None:
            raise case.submit_error
        ref = runtime.submit(JobSpec("operation.paratranz.download", "local", "digest"), owner).ref
        submitted.append(ref)
        runtime.schedule(ref, owner, lambda _cancellation: None)
        if case.complete_on_submit:
            queued[-1]()
        return ref

    features = tuple(
        OperationFeatureAdapter(mapper.kind, mapper, create, submit, lambda _draft, _fields: create(None, False, {}))
        for mapper in (
            DownloadOperationMapper(),
            UploadOperationMapper(),
            WriteOperationMapper(),
            FomodOperationMapper(),
        )
    )
    facade = OperationPlanFacade(
        SimpleNamespace(tasks=tasks),
        lambda _context: RequestContext("gui"),
        features,
        dialog_factories={OperationKind.DOWNLOAD: ParaTranzSyncDialog, OperationKind.UPLOAD: ParaTranzSyncDialog},
        execution_result_reader=lambda _ref, _owner: case.result,
    )

    def worker_factory(fn):
        worker = _DeferredWorker(fn)
        workers.append(worker)
        return worker

    facade._coordinator._preflight_worker_factory = worker_factory
    case = SimpleNamespace(
        facade=facade,
        tasks=tasks,
        owner=OwnerRef("gui", "gui.operation-plan"),
        queued=queued,
        workers=workers,
        submitted=submitted,
        dialog=None,
        submit_error=None,
        complete_on_submit=False,
        result=OperationResult.completed({}, counts=OperationCounts(succeeded=1)),
    )
    yield case
    if case.dialog is not None:
        case.dialog.set_preflight_running(False)
        case.dialog.set_execution_running(False)
        case.dialog.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
@pytest.mark.parametrize("terminal", [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED])
def test_sync_tracks_background_task_and_unlocks_only_at_terminal_state(sync_case, qapp, kind, terminal):
    case = sync_case
    begin = case.facade.begin_download if kind is OperationKind.DOWNLOAD else case.facade.begin_upload
    case.dialog = subject = begin(SimpleNamespace())
    qapp.processEvents()
    assert not case.workers
    subject._confirm.click()
    assert subject._cancel.isEnabled()
    case.workers[0].complete()
    assert subject._confirm.isEnabled()
    subject._confirm.click()
    subject._confirm.click()
    qapp.processEvents()

    assert len(case.submitted) == 1
    assert subject.isVisible()
    assert subject._progress.isVisible()
    assert case.facade.active_plan_count == 1
    assert not subject._cancel.isEnabled()
    ref = case.submitted[0]

    # Another task finishing must not unlock this page.
    other = case.tasks.submit(JobSpec("other", "other", "other"), case.owner).ref
    case.tasks.start(other, case.owner)
    case.tasks.complete(other, case.owner)
    qapp.processEvents()
    assert not subject._cancel.isEnabled()

    def finish():
        if terminal is JobState.FAILED:
            case.tasks.fail(ref, case.owner)
        elif terminal is JobState.CANCELLED:
            case.tasks.cancel(ref, case.owner)
            case.queued[0]()
        else:
            case.queued[0]()

    thread = Thread(target=finish)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert not subject._cancel.isEnabled()  # Worker events are queued onto the Qt thread.
    qapp.processEvents()
    assert subject._cancel.isEnabled()
    assert subject._choose_project.isEnabled()
    assert subject._confirm.text() == "重新对照"
    assert not case.tasks._subscriptions
    if terminal is JobState.COMPLETED:
        assert subject._progress.value() == 100
        assert "完成" in subject._status.text()
    else:
        assert not subject._progress.isVisible()
        assert ("失败" if terminal is JobState.FAILED else "取消") in subject._status.text()

    subject._confirm.click()
    assert len(case.submitted) == 1  # A consumed confirmation cannot run again.
    assert subject._cancel.isEnabled()
    case.workers[-1].complete()
    assert subject._confirm.isEnabled()
    subject._cancel.click()
    assert case.facade.active_plan_count == 0
    case.dialog = None


def test_task_finishing_before_subscription_still_unlocks_the_dialog(sync_case, qapp):
    case = sync_case
    case.complete_on_submit = True
    case.dialog = subject = case.facade.begin_download(SimpleNamespace())
    qapp.processEvents()
    subject._confirm.click()
    case.workers[0].complete()
    subject._confirm.click()
    qapp.processEvents()

    assert subject._cancel.isEnabled()
    assert subject._progress.value() == 100
    assert not case.tasks._subscriptions


def test_submission_failure_rechecks_and_does_not_leave_the_dialog_locked(sync_case, qapp):
    case = sync_case
    case.submit_error = ValueError("ParaTranz 内容已变化")
    case.dialog = subject = case.facade.begin_download(SimpleNamespace())
    qapp.processEvents()
    subject._confirm.click()
    case.workers[0].complete()
    subject._confirm.click()

    assert not case.submitted
    assert len(case.workers) == 2
    assert subject._progress.isVisible()
    case.workers[1].complete()
    assert subject._cancel.isEnabled()
    assert subject._confirm.isEnabled()
    assert not subject._progress.isVisible()


def test_parent_teardown_releases_subscription_without_cancelling_background_task(sync_case, qapp):
    case = sync_case
    case.dialog = subject = case.facade.begin_download(SimpleNamespace())
    qapp.processEvents()
    subject._confirm.click()
    case.workers[0].complete()
    subject._confirm.click()
    assert case.tasks._subscriptions
    subject.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    case.dialog = None

    assert case.facade.active_plan_count == 0
    assert not case.tasks._subscriptions
    case.queued[0]()
    assert case.tasks.get(case.submitted[0], case.owner).state is JobState.COMPLETED


def test_disabled_fields_remain_disabled_after_plan_refresh(dialog):
    restricted = replace(
        dialog._plan, editable_fields=tuple(replace(field, enabled=False) for field in dialog._plan.editable_fields)
    )
    dialog.render_plan(restricted)
    _ready(dialog)
    assert not dialog._choose_project.isEnabled()
    assert not dialog._apply_deletions.isEnabled()
    assert all(not button.isEnabled() for button in dialog._strategy_buttons.values())


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
@pytest.mark.parametrize("cancelled", [False, True])
def test_partial_receipt_is_delivered_with_counts_and_never_shown_as_full_success(sync_case, qapp, kind, cancelled):
    case = sync_case
    case.result = OperationResult.partial(
        {},
        counts=OperationCounts(succeeded=2, failed=1, skipped=3, cancelled=int(cancelled)),
        diagnostics=(Diagnostic("REMOTE_TIMEOUT", "部分条目连接超时"),),
    )
    begin = case.facade.begin_download if kind is OperationKind.DOWNLOAD else case.facade.begin_upload
    case.dialog = subject = begin(SimpleNamespace())
    qapp.processEvents()
    subject._confirm.click()
    case.workers[0].complete()
    subject._confirm.click()
    if cancelled:
        case.tasks.cancel(case.submitted[0], case.owner)
    case.queued[0]()
    qapp.processEvents()

    assert ("已取消" if cancelled else "部分完成") in subject._status.text()
    assert "成功 2 · 失败 1 · 跳过 3" in subject._status.text()
    assert "部分条目连接超时" in subject._status.text()
    assert "予以保留" in subject._status.text()
    assert "REMOTE_TIMEOUT" in subject._status.toolTip()
    assert not subject._progress.isVisible()
    assert subject._cancel.isEnabled() and subject._confirm.text() == "重新对照"
    subject._confirm.click()
    assert len(case.submitted) == 1


def test_missing_receipt_is_unknown_instead_of_full_success(sync_case, qapp):
    case = sync_case
    case.result = None
    case.dialog = subject = case.facade.begin_download(SimpleNamespace())
    qapp.processEvents()
    subject._confirm.click()
    case.workers[0].complete()
    subject._confirm.click()
    case.queued[0]()
    qapp.processEvents()
    assert "结果待核验" in subject._status.text()
    assert not subject._progress.isVisible()
    assert subject._cancel.isEnabled()


def _review_rows() -> tuple[SyncReviewRow, ...]:
    return (
        SyncReviewRow(
            "local-snapshot",
            "duplicate-key",
            "Original",
            "本地译文",
            "local-context",
            "存在多个云端候选",
            (
                SyncReviewCandidate("candidate-a", "cloud.json · ID 7", "Original", "云端译文 A"),
                SyncReviewCandidate("candidate-b", "cloud.json · ID 8", "Original", "云端译文 B"),
            ),
        ),
    )


@pytest.mark.parametrize("operation", [SyncOperation.DOWNLOAD, SyncOperation.UPLOAD])
@pytest.mark.parametrize("availability", ["none", "blocked", "available"])
def test_review_guidance_only_suggests_choices_that_are_available(operation, availability):
    entry = SimpleNamespace(
        entry_key=SimpleNamespace(local_key="test-key", original=None),
        original="Original",
        translation="译文",
        context="对话",
        stage=5,
        external_ref=SimpleNamespace(opaque_id=7),
    )
    candidates = (
        ()
        if availability == "none"
        else (
            SimpleNamespace(
                token="cloud",
                entry=entry,
                selectable=availability == "available",
                reason="remote_already_linked",
                source_count=1,
            ),
        )
    )
    row = SimpleNamespace(
        row_id="local",
        local=entry,
        candidates=candidates,
        reason="stale_remote_reference" if operation is SyncOperation.UPLOAD else "original_match_required",
        allow_remote_choice=availability == "available",
        resolved=False,
    )
    plan = SimpleNamespace(operation=operation, download_review=(row,), upload_review=(row,))
    projected = review_rows(plan)[0]

    assert projected.preserves_local_source
    assert projected.stage == 5
    assert "候选" not in projected.reason and "关联" not in projected.reason
    if availability == "available":
        assert "请对照原文和补充说明" in projected.reason
        assert projected.candidates[0].stage == 5
    else:
        assert "请选择" not in projected.reason and "选择你要" not in projected.reason
        assert ("先不上传" if operation is SyncOperation.UPLOAD else "先保留你的译文") in projected.reason


def _render_review(subject, *, actionable: int = 2, rows=None) -> None:
    download = subject._plan.kind is OperationKind.DOWNLOAD
    impact = (
        (("update_local", actionable), ("keep_local", 3), ("remote_only", 4), ("deferred", 1))
        if download
        else (("update_remote", actionable), ("keep_remote", 3), ("local_only", 4), ("deferred", 1))
    )
    subject.render_preflight(
        OperationPreflightResult(
            subject._plan.kind,
            subject._plan.request_digest,
            "remote:18668:7",
            (PreflightCheckState("conflicts", "待处理项", PreflightCheckStatus.WARNING, "发现不能自动对应的条目"),),
            (),
            object(),
            impact,
            sync_review=_review_rows() if rows is None else rows,
        )
    )
    subject.set_preflight_running(False)


def test_conflict_notice_is_not_repeated_but_other_warnings_remain_visible(dialog):
    scope_warning = "云端多出的内容不会下载。"
    dialog.render_preflight(
        OperationPreflightResult(
            OperationKind.DOWNLOAD,
            dialog._plan.request_digest,
            "remote:7",
            (
                PreflightCheckState(
                    "PARATRANZ_PLAN_FRESH", "翻译确认", PreflightCheckStatus.WARNING, "重复的待确认提示"
                ),
                PreflightCheckState(
                    "PARATRANZ_LOCAL_SOURCE_SCOPE", "下载范围", PreflightCheckStatus.WARNING, scope_warning
                ),
            ),
            (),
            object(),
            (("update_local", 2), ("deferred", 1)),
            sync_review=_review_rows(),
        )
    )
    dialog.set_preflight_running(False)

    assert "重复的待确认提示" not in dialog._status.text()
    assert scope_warning in dialog._status.text()
    assert dialog._review.isEnabled() and dialog._confirm.isEnabled()


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_sync_can_confirm_safe_updates_with_actionable_review_entry(dialog, kind):
    dialog.render_plan(replace(dialog._plan, kind=kind))
    _render_review(dialog)
    assert dialog._confirm.isEnabled()
    download = kind is OperationKind.DOWNLOAD
    assert dialog._confirm.text() == ("更新可确定的工程译文" if download else "上传可确定的译文")
    assert dialog._review.isVisible() and dialog._review.isEnabled()
    assert dialog._review.text() == "确认 1 条翻译…"
    assert ("保留本地 3" if download else "保留云端 3") in dialog._impact.text()
    assert ("不下载 4" if download else "不上传 4") in dialog._impact.text()
    assert "待确认 1" in dialog._impact.text()
    assert ("保持本地不变" if download else "保持云端不变") in dialog._status.text()
    confirmed = []
    dialog.confirm_requested.connect(lambda *_args: confirmed.append(True))
    dialog._confirm.click()
    assert confirmed == [True]


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_all_deferred_sync_guides_review_instead_of_claiming_up_to_date(dialog, kind):
    dialog.render_plan(replace(dialog._plan, kind=kind))
    _render_review(dialog, actionable=0)
    assert not dialog._confirm.isEnabled()
    assert dialog._review.isEnabled()
    assert "请点击下方按钮查看" in dialog._status.text()
    assert "已一致" not in dialog._status.text()
    assert "无需更新" not in dialog._status.text()


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_review_applies_decisions_via_new_preflight_without_confirming_execution(dialog, monkeypatch, qapp, kind):
    dialog.render_plan(replace(dialog._plan, kind=kind))
    _render_review(dialog)
    requested, executed = [], []
    dialog.preflight_requested.connect(lambda _session, values: requested.append(dict(values)))
    dialog.confirm_requested.connect(lambda *_args: executed.append(True))

    def choose(subject):
        subject._table.selectRow(0)
        subject._detail.candidates.setCurrentIndex(2)
        subject._detail.use_remote.click()
        return 1

    monkeypatch.setattr(SyncReviewDialog, "exec", choose)
    dialog._review.click()
    assert not dialog._review.isEnabled()
    assert not dialog._confirm.isEnabled()
    qapp.processEvents()
    assert len(requested) == 1
    field_id = "download_decisions" if kind is OperationKind.DOWNLOAD else "upload_decisions"
    assert json.loads(requested[0][field_id]) == {"local-snapshot": "candidate-b"}
    assert ("upload_decisions" if kind is OperationKind.DOWNLOAD else "download_decisions") not in requested[0]
    assert not executed


def test_review_cancel_preserves_decisions_and_does_not_trigger_preflight(dialog, monkeypatch):
    _render_review(dialog)
    requested = []
    dialog.preflight_requested.connect(lambda *_args: requested.append(True))

    def cancel(subject):
        subject._table.selectRow(0)
        subject._detail.keep.click()
        return 0

    monkeypatch.setattr(SyncReviewDialog, "exec", cancel)
    dialog._review.click()
    assert json.loads(dict(dialog.edited_values())["download_decisions"]) == {}
    assert not requested
    assert dialog._confirm.isEnabled()


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_preflight_replaces_old_decisions_with_validated_snapshot(dialog, kind):
    field_id = "download_decisions" if kind is OperationKind.DOWNLOAD else "upload_decisions"
    keep = KEEP_LOCAL if kind is OperationKind.DOWNLOAD else KEEP_REMOTE
    plan = replace(
        dialog._plan,
        kind=kind,
        editable_fields=dialog._plan.editable_fields
        + (EditableFieldState(field_id, "本次处理选择", '{"old-snapshot":"candidate-a"}'),),
    )
    dialog.render_plan(plan)
    assert json.loads(dict(dialog.edited_values())[field_id]) == {"old-snapshot": "candidate-a"}
    _render_review(dialog, rows=(replace(_review_rows()[0], decision=keep),))
    assert json.loads(dict(dialog.edited_values())[field_id]) == {"local-snapshot": keep}


@pytest.mark.parametrize("phase", ["preflight", "execution"])
@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_review_is_unavailable_while_busy(dialog, monkeypatch, phase, kind):
    dialog.render_plan(replace(dialog._plan, kind=kind))
    _render_review(dialog)
    entered = []
    monkeypatch.setattr(SyncReviewDialog, "exec", lambda self: entered.append(True))
    if phase == "preflight":
        dialog.set_preflight_running(True)
    else:
        dialog.set_execution_running(True)
    assert not dialog._review.isEnabled()
    dialog._review_items()
    assert not entered


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_completed_sync_retains_actionable_review_and_reports_deferred_separately(dialog, monkeypatch, qapp, kind):
    dialog.render_plan(replace(dialog._plan, kind=kind))
    _render_review(dialog)
    dialog.set_execution_running(True)
    activity = SimpleNamespace(is_terminal=True, state=JobState.COMPLETED, run_id="run", diagnostic_refs=())
    dialog.render_execution(
        activity,
        OperationResult.completed({"deferred_count": 1}, counts=OperationCounts(succeeded=2, skipped=1)),
    )
    assert dialog._review.isEnabled()
    assert ("已更新 2 · 暂缓 1" if kind is OperationKind.DOWNLOAD else "已上传 2 · 暂缓 1") in dialog._status.text()
    assert "失败" not in dialog._status.text()
    requested = []
    dialog.preflight_requested.connect(lambda _session, values: requested.append(dict(values)))

    def keep(subject):
        subject._table.selectRow(0)
        subject._detail.keep.click()
        return 1

    monkeypatch.setattr(SyncReviewDialog, "exec", keep)
    dialog._review.click()
    qapp.processEvents()
    field_id = "download_decisions" if kind is OperationKind.DOWNLOAD else "upload_decisions"
    keep_choice = KEEP_LOCAL if kind is OperationKind.DOWNLOAD else KEEP_REMOTE
    assert json.loads(requested[0][field_id]) == {"local-snapshot": keep_choice}
    _render_review(dialog, rows=(replace(_review_rows()[0], decision=keep_choice),))
    assert dialog._confirm.isEnabled()


@pytest.mark.parametrize("state", [JobState.COMPLETED, JobState.FAILED])
def test_authoritative_download_reports_saved_only_after_success_and_keeps_retry(dialog, state):
    dialog._context.uses_authoritative_projection = True
    _ready(dialog)
    dialog.set_execution_running(True)
    activity = SimpleNamespace(is_terminal=True, state=state, run_id="run", diagnostic_refs=())
    result = (
        OperationResult.completed({}, counts=OperationCounts(succeeded=1))
        if state is JobState.COMPLETED
        else OperationResult.failed(
            DomainError(ErrorCategory.EXTERNAL, "LOCAL_TRANSACTION_FAILED", "自动保存磁盘写入失败")
        )
    )
    dialog.render_execution(activity, result)
    if state is JobState.COMPLETED:
        assert "已自动保存到当前本地版本" in dialog._status.text()
    else:
        assert "自动保存磁盘写入失败" in dialog._status.text()
        assert "已自动保存" not in dialog._status.text()
        assert dialog._confirm.isEnabled()
        assert dialog._confirm.text() == "重新对照"
        assert not dialog._execution_running


def test_unchanged_authoritative_download_cannot_run_or_claim_automatic_save(dialog):
    dialog._context.uses_authoritative_projection = True
    requested = []
    dialog.confirm_requested.connect(lambda *_args: requested.append(True))
    dialog.render_preflight(
        OperationPreflightResult(
            OperationKind.DOWNLOAD,
            dialog._plan.request_digest,
            "remote:7",
            (),
            (),
            object(),
            (("update_local", 0), ("keep_local", 1)),
        )
    )
    dialog.set_preflight_running(False)
    assert not dialog._confirm.isEnabled()
    dialog._confirm.click()
    assert not requested
    assert "已自动保存" not in dialog._status.text()


def test_upload_blocking_check_still_disables_execution(dialog):
    plan = replace(dialog._plan, kind=OperationKind.UPLOAD)
    dialog.render_plan(plan)
    dialog.render_preflight(
        OperationPreflightResult(
            OperationKind.UPLOAD,
            plan.request_digest,
            "remote:7",
            (PreflightCheckState("permission", "权限", PreflightCheckStatus.BLOCKED, "当前用户没有上传权限"),),
            (),
            None,
            (("update_remote", 2), ("conflict", 1)),
        )
    )
    dialog.set_preflight_running(False)
    assert not dialog._check_ready
    assert dialog._confirm.text() == "重新对照"
    assert not dialog._review.isVisible()
    assert "download_decisions" not in dict(dialog.edited_values())


def test_remote_project_and_status_are_literal_text(dialog):
    plan = replace(dialog._plan, title="<b>Project</b>", scope_summary="<img src='example'>")
    dialog.render_plan(plan)
    dialog.render_preflight_error("<b>Untrusted error</b>")
    for label in (dialog._headline, dialog._scope, dialog._target_name, dialog._status):
        assert label.textFormat() is Qt.TextFormat.PlainText
    assert dialog._status.text() == "<b>Untrusted error</b>"


def test_preflight_drops_now_unavailable_candidate_decision(dialog):
    row = _review_rows()[0]
    candidate = replace(row.candidates[0], selectable=False, disabled_reason="已绑定到另一条本地记录")
    _render_review(dialog, rows=(replace(row, candidates=(candidate,), decision=candidate.candidate_id),))
    assert json.loads(dict(dialog.edited_values())["download_decisions"]) == {}


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_completed_manual_update_removes_applied_choice_before_reviewing_remaining_row(dialog, monkeypatch, qapp, kind):
    dialog.render_plan(replace(dialog._plan, kind=kind))
    row_a = replace(_review_rows()[0], decision="candidate-a")
    row_b = replace(_review_rows()[0], row_id="second-local-snapshot", key="second-key")
    _render_review(dialog, rows=(row_a, row_b))
    dialog.set_execution_running(True)
    dialog.render_execution(
        SimpleNamespace(is_terminal=True, state=JobState.COMPLETED, run_id="run", diagnostic_refs=()),
        OperationResult.completed({"deferred_count": 1}, counts=OperationCounts(succeeded=2, skipped=1)),
    )
    assert dialog._review_rows == (row_b,)
    field_id = "download_decisions" if kind is OperationKind.DOWNLOAD else "upload_decisions"
    assert json.loads(dict(dialog.edited_values())[field_id]) == {}
    requested = []
    dialog.preflight_requested.connect(lambda _session, values: requested.append(dict(values)))

    def choose_remaining(subject):
        assert subject._model.rowCount() == 1
        subject._table.selectRow(0)
        subject._detail.candidates.setCurrentIndex(2)
        subject._detail.use_remote.click()
        return 1

    monkeypatch.setattr(SyncReviewDialog, "exec", choose_remaining)
    dialog._review.click()
    qapp.processEvents()
    assert json.loads(requested[0][field_id]) == {"second-local-snapshot": "candidate-b"}


@pytest.mark.parametrize("state", [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED])
@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_unverified_execution_rechecks_before_accepting_further_review_choices(dialog, monkeypatch, state, kind):
    dialog.render_plan(replace(dialog._plan, kind=kind))
    row = replace(_review_rows()[0], decision="candidate-a")
    _render_review(dialog, rows=(row,))
    dialog.set_execution_running(True)
    dialog.render_execution(SimpleNamespace(is_terminal=True, state=state, run_id="run", diagnostic_refs=()), None)
    assert dialog._review_rows == (row,)
    assert dialog._review.text() == "刷新这 1 条翻译…"
    requested, entered = [], []
    dialog.preflight_requested.connect(lambda *_args: requested.append(True))
    monkeypatch.setattr(SyncReviewDialog, "exec", lambda self: entered.append(True))
    dialog._review.click()
    assert requested == [True]
    assert not entered


def test_upload_keep_choices_do_not_claim_cloud_matches_local(dialog):
    dialog.render_plan(replace(dialog._plan, kind=OperationKind.UPLOAD))
    dialog.render_preflight(
        OperationPreflightResult(
            OperationKind.UPLOAD,
            dialog._plan.request_digest,
            "remote:7",
            (),
            (),
            object(),
            (("update_remote", 0), ("keep_remote", 3), ("local_only", 2), ("deferred", 0)),
            sync_review=(replace(_review_rows()[0], decision=KEEP_REMOTE),),
        )
    )
    dialog.set_preflight_running(False)
    assert not dialog._confirm.isEnabled()
    assert dialog._review.isEnabled()
    assert "按当前选择无需上传" in dialog._status.text()
    assert "已一致" not in dialog._status.text()


def test_upload_partial_success_explains_remote_writes_and_requires_refresh_before_review(dialog, monkeypatch):
    dialog.render_plan(replace(dialog._plan, kind=OperationKind.UPLOAD))
    row = replace(_review_rows()[0], decision="candidate-a")
    _render_review(dialog, rows=(row,))
    dialog.set_execution_running(True)
    dialog.render_execution(
        SimpleNamespace(is_terminal=True, state=JobState.COMPLETED, run_id="run", diagnostic_refs=()),
        OperationResult.partial(
            {"deferred_count": 1},
            counts=OperationCounts(succeeded=2, failed=1, skipped=1),
            diagnostics=(Diagnostic("UPLOAD_ASSOCIATION_COMMIT_FAILED", "Could not commit local upload associations"),),
        ),
    )
    assert "已上传 2 · 暂缓 1（保持云端不变）" in dialog._status.text()
    assert "云端更改已提交，但本地云端关联未保存" in dialog._status.text()
    assert "Could not commit local upload associations" not in dialog._status.text()
    assert "Could not commit local upload associations" in dialog._status.toolTip()
    assert "不会自动回滚" in dialog._status.text()
    assert dialog._review_rows == (row,)
    requested, entered = [], []
    dialog.preflight_requested.connect(lambda *_args: requested.append(True))
    monkeypatch.setattr(SyncReviewDialog, "exec", lambda self: entered.append(True))
    dialog._review.click()
    assert requested == [True]
    assert not entered
