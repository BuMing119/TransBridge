"""The registered switch tool and desktop picker bind the same local project."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident
from time import monotonic
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from tests.conftest import make_test_collection
from transbridge.application.ports.paratranz import ParaTranzProject
from transbridge.application.projects import ParaTranzProjectBinding, ProjectRemoteBindingService
from transbridge.application.tasks.controls import CancellationToken, TaskCancelled
from transbridge.config.paratranz import ParatranzConfig
from transbridge.paratranz.project_catalog import ParaTranzCatalogKey
from transbridge.paratranz.service import ParaTranzService
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import register_all
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.workbench.remote_target_view import ParaTranzTargetDialog, RemoteTargetView


def _wait_until(ready):
    deadline = monotonic() + 5
    while not ready() and monotonic() < deadline:
        QTest.qWait(5)
    assert ready()


def _confirm_picker(dialog, modal_exec):
    deadline = QTimer(dialog)
    deadline.setSingleShot(True)
    deadline.timeout.connect(dialog.reject)
    deadline.start(5000)
    QTimer.singleShot(0, dialog._ok.click)
    result = modal_exec(dialog)
    deadline.stop()
    return result


@pytest.fixture(scope="module")
def binding_qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def binding_app(binding_qapp, current_project_factory):
    project = current_project_factory(make_test_collection(1))
    app = project.context
    app._project_remote_bindings = ProjectRemoteBindingService(project.harness.service)
    app.config = ParatranzConfig(token="test", environment={}, user_id=7)
    register_all()
    return app


def test_desktop_selection_and_assistant_switch_share_binding_and_membership(binding_app, monkeypatch):
    app = binding_app
    owner = get_ident()
    reads, writes, closes = [], [], []
    app.paratranz_binding_changed.connect(lambda binding: writes.append((get_ident(), binding)))

    def list_projects(*, uid, cancellation):
        reads.append((get_ident(), uid))
        name = "Old name" if len(reads) == 1 else "New name"
        return (ParaTranzProject(101, "First"), ParaTranzProject(202, name))

    monkeypatch.setattr(
        ParaTranzService,
        "from_config",
        lambda config: SimpleNamespace(list_projects=list_projects, close=lambda: closes.append(config.user_id)),
    )

    modal_exec = ParaTranzTargetDialog.exec

    def choose_second(dialog):
        _wait_until(lambda: not dialog._workers and dialog._list.count() == 2)
        dialog._list.setCurrentRow(1)
        assert dialog.selected_project()["name"] == "Old name"
        return _confirm_picker(dialog, modal_exec)

    monkeypatch.setattr(ParaTranzTargetDialog, "exec", choose_second)
    view = RemoteTargetView(app)
    try:
        view.choose_target()
        assert "New name" in view.state.summary
        assert app.paratranz_binding["project_id"] == 202
        app.current_project = {"id": 101, "name": "First"}
        read = ToolRegistry.get("get_paratranz_project")
        assert read.execute({}, ExecutionContext(app)).data["id"] == 202
        revision = app.project_revision
        with ThreadPoolExecutor(max_workers=1) as pool:
            tool = ToolRegistry.get("switch_paratranz_project")
            future = pool.submit(tool.execute, {"project_id": 101}, ExecutionContext(app))
            _wait_until(future.done)
            result = future.result()
        assert result.success, result.message
        assert result.data["name"] == "First" and "First" in view.state.summary
        assert read.execute({}, ExecutionContext(app)).data["id"] == 101
        assert app.project_revision == revision + 1
        assert [binding["project_id"] for _, binding in writes] == [202, 101]
        assert all(tid == owner and binding["account_user_id"] == 7 for tid, binding in writes)
        assert len(reads) == 3 and all(tid != owner and uid == "my" for tid, uid in reads)
        assert closes == [7, 7, 7]
    finally:
        view.close()
        view.deleteLater()


def test_desktop_cached_project_losing_membership_before_confirmation_is_not_bound(binding_app, monkeypatch):
    app = binding_app
    view = RemoteTargetView(app)
    revision = app.project_revision
    key = ParaTranzCatalogKey(app.config.base_url, 7, 0)
    reads, notices = [], []

    def list_projects(*, uid, cancellation):
        reads.append(uid)
        return (ParaTranzProject(202, "Old name"),) if len(reads) == 1 else ()

    service = SimpleNamespace(list_projects=list_projects, close=lambda: None)
    monkeypatch.setattr(ParaTranzService, "from_config", lambda _: service)
    view._catalog.list_my_projects(service, key)
    modal_exec = ParaTranzTargetDialog.exec

    def choose_cached(dialog):
        _wait_until(lambda: not dialog._workers and dialog._list.count() == 1)
        dialog._list.setCurrentRow(0)
        assert dialog.selected_project()["id"] == 202 and reads == ["my"]
        timer = QTimer(dialog)

        def dismiss_denial():
            if "不是所选项目的成员" in dialog._status.text():
                notices.append(dialog._status.text())
                dialog.reject()

        timer.timeout.connect(dismiss_denial)
        timer.start(5)
        result = _confirm_picker(dialog, modal_exec)
        timer.stop()
        return result

    monkeypatch.setattr(ParaTranzTargetDialog, "exec", choose_cached)
    try:
        view.choose_target()
        assert notices and reads == ["my", "my"]
        assert app.paratranz_binding is None and app.project_revision == revision
    finally:
        view.close()
        view.deleteLater()


@pytest.mark.parametrize("change", ["non_member", "cancelled", "rebound", "reopened", "account", "endpoint"])
def test_assistant_rejects_stale_or_unavailable_selection(binding_app, monkeypatch, change):
    app = binding_app
    waiting, resume, cancelled = Event(), Event(), Event()
    execution = ExecutionContext(app)
    execution.cancellation_token = CancellationToken(cancelled)
    revision = app.project_revision

    def list_projects(**_):
        waiting.set()
        assert resume.wait(5)
        return () if change == "non_member" else (ParaTranzProject(101, "First"),)

    monkeypatch.setattr(
        ParaTranzService, "from_config", lambda _: SimpleNamespace(list_projects=list_projects, close=lambda: None)
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ToolRegistry.get("switch_paratranz_project").execute, {"project_id": 101}, execution)
        try:
            _wait_until(waiting.is_set)
            if change == "cancelled":
                cancelled.set()
            elif change == "rebound":
                assert app.set_paratranz_binding(ParaTranzProjectBinding(202, "Second", app.config.base_url)).is_success
            elif change == "reopened":
                assert app.finish_project_open(app.begin_project_open())
            elif change == "account":
                app.current_user = {"id": 8}
            elif change == "endpoint":
                app.config.base_url = "https://example.org/api"
        finally:
            resume.set()
        _wait_until(future.done)
        if change == "cancelled":
            with pytest.raises(TaskCancelled):
                future.result()
        else:
            result = future.result()
            assert not result.success, result.message
    if change == "rebound":
        assert app.paratranz_binding["project_id"] == 202 and app.project_revision == revision + 1
    else:
        assert app.paratranz_binding is None and app.project_revision == revision


def test_cancelling_after_membership_read_discards_queued_binding(binding_app, monkeypatch):
    app = binding_app
    revision = app.project_revision
    cancelled = Event()
    execution = ExecutionContext(app)
    execution.cancellation_token = CancellationToken(cancelled)
    service = SimpleNamespace(list_projects=lambda **_: (ParaTranzProject(101, "First"),), close=lambda: None)
    monkeypatch.setattr(ParaTranzService, "from_config", lambda _: service)
    queued = []
    monkeypatch.setattr(app, "safe_mutate", queued.append)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(ToolRegistry.get("switch_paratranz_project").execute, {"project_id": 101}, execution)
        _wait_until(lambda: bool(queued))
        cancelled.set()
        queued.pop()()
        with pytest.raises(TaskCancelled):
            future.result(timeout=5)
    assert app.paratranz_binding is None and app.project_revision == revision
