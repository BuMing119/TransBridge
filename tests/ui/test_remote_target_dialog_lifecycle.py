from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.projects import ParaTranzTargetSource, ParaTranzTargetStatus, ResolvedParaTranzTarget
import transbridge.ui.workbench.remote_target_view as remote_target_view


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Signal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self._callbacks):
            callback(*args)


class _ControlledWorker:
    instances = []

    def __init__(self, _fn) -> None:
        self.result = _Signal()
        self.error = _Signal()
        self.finished = _Signal()
        self.running = False
        self.deleted = False
        self.instances.append(self)

    def start(self) -> None:
        self.running = True

    def isRunning(self) -> bool:  # noqa: N802 - QThread compatibility
        return self.running

    def deleteLater(self) -> None:  # noqa: N802 - QObject compatibility
        self.deleted = True

    def finish(self) -> None:
        self.running = False
        self.finished.emit()


def test_reject_cancels_catalog_request_and_defers_destruction_until_worker_finishes(qapp, monkeypatch) -> None:
    _ControlledWorker.instances.clear()
    monkeypatch.setattr(remote_target_view, "ApiWorker", _ControlledWorker)
    context = SimpleNamespace(
        config=SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=7),
        current_user={"id": 7},
    )
    dialog = remote_target_view.ParaTranzTargetDialog(
        context,
        remote_target_view.ParaTranzProjectCatalog(),
        0,
    )
    worker = _ControlledWorker.instances[-1]

    dialog.reject()

    assert dialog._closing is True
    assert dialog._cancellation is not None and dialog._cancellation.is_cancelled
    assert worker in dialog._workers

    worker.finish()

    assert dialog._closing is False
    assert dialog._workers == []
    assert worker.deleted is True
    dialog.deleteLater()
    qapp.processEvents()


@pytest.fixture
def binding_view(qapp):
    operations = []
    context = SimpleNamespace(
        active_project_id="local-project",
        project_opening=False,
        project_view_failed=False,
        config=SimpleNamespace(token="configured", base_url="https://paratranz.cn", user_id=7),
        current_user={"id": 7},
        paratranz_binding_changed=_Signal(),
        config_changed=_Signal(),
        user_changed=_Signal(),
        project_changed=_Signal(),
        resolve_paratranz_target=lambda: ResolvedParaTranzTarget(
            42,
            "远端项目",
            "https://paratranz.cn",
            7,
            ParaTranzTargetSource.PROJECT_BINDING,
            ParaTranzTargetStatus.AVAILABLE,
        ),
        set_paratranz_binding=lambda binding: operations.append(binding) or SimpleNamespace(is_success=True),
        clear_paratranz_binding=lambda: operations.append("clear") or SimpleNamespace(is_success=True),
    )
    view = remote_target_view.RemoteTargetView(context)
    yield context, view, operations
    view.close()


@pytest.mark.parametrize("change", ["other_project", "closed_project", "switched_back", "opening", "failed_view"])
def test_picker_does_not_bind_after_local_project_context_changes(binding_view, monkeypatch, change):
    context, view, operations = binding_view
    messages = []

    def choose():
        if change == "other_project":
            context.active_project_id = "another-project"
        elif change == "closed_project":
            context.active_project_id = None
        elif change == "switched_back":
            context.active_project_id = "another-project"
            context.project_changed.emit()
            context.active_project_id = "local-project"
        elif change == "opening":
            context.project_opening = True
        else:
            context.project_view_failed = True
        context.project_changed.emit()
        return 1

    monkeypatch.setattr(
        remote_target_view,
        "ParaTranzTargetDialog",
        lambda *_: SimpleNamespace(
            exec=choose,
            selected_project=lambda: {"id": 99, "name": "选择的项目"},
            target_context=("https://paratranz.cn", 7, 0),
        ),
    )
    monkeypatch.setattr(remote_target_view.QMessageBox, "warning", lambda _parent, title, _text: messages.append(title))
    view.choose_target()
    assert operations == []
    assert messages == ["工程已变化"]


@pytest.mark.parametrize("change", ["account", "endpoint", "configuration"])
def test_picker_preserves_account_and_endpoint_change_guard(binding_view, monkeypatch, change):
    context, view, operations = binding_view
    messages = []

    def choose():
        if change == "account":
            context.current_user = {"id": 8}
        elif change == "endpoint":
            context.config.base_url = "https://example.com"
        else:
            context.config_changed.emit(context.config)
        return 1

    monkeypatch.setattr(
        remote_target_view,
        "ParaTranzTargetDialog",
        lambda *_: SimpleNamespace(
            exec=choose,
            selected_project=lambda: {"id": 99, "name": "选择的项目"},
            target_context=("https://paratranz.cn", 7, 0),
        ),
    )
    monkeypatch.setattr(remote_target_view.QMessageBox, "warning", lambda _parent, title, _text: messages.append(title))
    view.choose_target()
    assert operations == []
    assert messages == ["配置已变化"]


@pytest.mark.parametrize("unavailable", ["no_project", "opening", "failed_view"])
def test_binding_actions_require_available_local_project(binding_view, monkeypatch, unavailable):
    context, view, operations = binding_view
    if unavailable == "no_project":
        context.active_project_id = None
    elif unavailable == "opening":
        context.project_opening = True
    else:
        context.project_view_failed = True
    view.refresh()
    monkeypatch.setattr(
        remote_target_view, "ParaTranzTargetDialog", lambda *_: pytest.fail("不可用工程不能打开绑定窗口")
    )
    assert not view._choose.isEnabled()
    assert not view._clear.isEnabled()
    view.choose_target()
    view.clear_target()
    assert operations == []


def test_inline_binding_and_overflow_clear_use_existing_local_commands_once(binding_view, monkeypatch):
    context, view, operations = binding_view
    signals = []
    view.binding_changed.connect(lambda: signals.append(True))
    monkeypatch.setattr(
        remote_target_view,
        "ParaTranzTargetDialog",
        lambda *_: SimpleNamespace(
            exec=lambda: 1,
            selected_project=lambda: {"id": 99, "name": "选择的项目"},
            target_context=("https://paratranz.cn", 7, 0),
        ),
    )
    view._choose.click()
    assert len(operations) == 1
    binding = operations[0]
    assert binding.project_id == 99
    assert binding.project_name == "选择的项目"
    assert binding.account_user_id == context.current_user["id"]
    assert signals == [True]
    view._more.menu().actions()[0].trigger()
    assert operations == [binding, "clear"]
    assert signals == [True, True]


def test_open_binding_menu_closes_when_local_project_changes(binding_view, qapp):
    context, view, operations = binding_view
    view.show()
    menu = view._more.menu()
    menu.popup(view._more.mapToGlobal(view._more.rect().bottomLeft()))
    qapp.processEvents()
    assert menu.isVisible()
    context.active_project_id = "another-project"
    context.project_changed.emit()
    qapp.processEvents()
    assert not menu.isVisible()
    assert operations == []
