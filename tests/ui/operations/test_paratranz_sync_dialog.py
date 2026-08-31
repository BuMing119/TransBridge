from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import os
from threading import Thread
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton
import pytest

from tests.ui.operations.test_operation_plan_coordinator_async import _DeferredWorker
from transbridge.application.contracts import RequestContext
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


def test_dialog_uses_project_name_and_automatically_requests_preflight(qapp) -> None:
    context = SimpleNamespace(config=SimpleNamespace(config_revision=0), active_project_id="local")
    dialog = ParaTranzSyncDialog(_plan(), context)
    requested = []
    dialog.preflight_requested.connect(lambda session_id, values: requested.append((session_id, dict(values))))
    dialog.show()
    qapp.processEvents()

    assert dialog._target_name.text() == "天际中文翻译"
    assert "18668" not in dialog._target_name.text()
    assert not dialog.findChildren(QLineEdit)
    assert dialog._confirm.text() == "下载并更新本地"
    assert requested and requested[-1][0] == "session"
    assert requested[-1][1] == {
        "paratranz_project_id": "18668",
        "paratranz_project_name": "天际中文翻译",
        "set_as_default": "false",
        "conflict_policy": "prefer_remote",
        "apply_remote_deletions": "false",
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
    assert dialog._status.text() == "检查完成，可以开始。"
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


@pytest.mark.parametrize("phase", ["preflight", "execution"])
def test_busy_dialog_shows_progress_and_blocks_all_controls_and_close(dialog, qapp, phase):
    if phase == "execution":
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


def test_preflight_unlock_restores_field_permissions_and_change_locks_immediately(dialog, qapp):
    _ready(dialog)
    assert not dialog._progress.isVisible()
    assert dialog._choose_project.isEnabled()
    assert dialog._apply_deletions.isEnabled()
    assert not dialog._set_default.isEnabled()
    assert dialog._cancel.isEnabled()
    requested = []
    dialog.preflight_requested.connect(lambda _session, fields: requested.append(dict(fields)))
    dialog._strategy_buttons["prefer_local"].click()

    assert dialog._progress.isVisible()
    assert not dialog._confirm.isEnabled()
    assert not dialog._cancel.isEnabled()
    qapp.processEvents()
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
    assert dialog._confirm.text() == "重新检查"
    requested = []
    dialog.preflight_requested.connect(lambda *_args: requested.append(True))
    dialog._confirm.click()
    assert requested == [True]
    assert dialog._progress.isVisible()
    assert not dialog._cancel.isEnabled()


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
    assert not subject._cancel.isEnabled()
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
    assert subject._confirm.text() == "重新检查"
    assert not case.tasks._subscriptions
    if terminal is JobState.COMPLETED:
        assert subject._progress.value() == 100
        assert "完成" in subject._status.text()
    else:
        assert not subject._progress.isVisible()
        assert ("失败" if terminal is JobState.FAILED else "取消") in subject._status.text()

    subject._confirm.click()
    assert len(case.submitted) == 1  # A consumed confirmation cannot run again.
    assert not subject._cancel.isEnabled()
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
