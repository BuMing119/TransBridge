from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem
import pytest

from transbridge.application.ports.paratranz import ParaTranzProject
import transbridge.ui.paratranz.project_panel as project_panel


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

    def __init__(self, fn, *args, **kwargs) -> None:
        del args, kwargs
        self._fn = fn
        self.result = _Signal()
        self.error = _Signal()
        self.finished = _Signal()
        self.deleted = False
        self.instances.append(self)

    def start(self) -> None:
        pass

    def deleteLater(self) -> None:  # noqa: N802 - QObject compatibility
        self.deleted = True

    def complete(self) -> None:
        self.result.emit(self._fn())
        self.finished.emit()


class _Context:
    def __init__(self) -> None:
        self.config = SimpleNamespace(token="", base_url="https://paratranz.cn", user_id=7)
        self.current_user = {"id": 7}
        self.active_project_id = "local-project"
        self.project_revision = 3
        self.current_project = None
        self.mine_project_ids = set()
        self.config_changed = _Signal()
        self.project_list_changed = _Signal()
        self.bindings = []

    def set_paratranz_binding(self, binding):
        self.bindings.append(binding)
        return SimpleNamespace(is_success=True, diagnostics=())


class _Service:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Catalog:
    def __init__(self, projects) -> None:
        self.projects = tuple(projects)

    def list_my_projects(self, _service, _key, *, refresh=False):
        assert refresh is True
        return SimpleNamespace(projects=self.projects)

    def clear(self) -> None:
        pass


def _panel(monkeypatch, projects):
    _ControlledWorker.instances.clear()
    monkeypatch.setattr(project_panel, "ApiWorker", _ControlledWorker)
    context = _Context()
    panel = project_panel.ProjectListPanel(context)
    context.config.token = "configured"
    service = _Service()
    monkeypatch.setattr(project_panel, "ParaTranzService", SimpleNamespace(from_config=lambda _config: service))
    panel._catalog = _Catalog(projects)
    item = QListWidgetItem("Remote")
    item.setData(Qt.ItemDataRole.UserRole, {"id": 42, "name": "Remote"})
    panel._list.addItem(item)
    panel._list.setCurrentItem(item)
    return panel, context, service


def test_binding_waits_for_async_membership_validation_and_closes_service(qapp, monkeypatch) -> None:
    infos = []
    warnings = []
    monkeypatch.setattr(project_panel.QMessageBox, "information", lambda *_args: infos.append(_args))
    monkeypatch.setattr(project_panel.QMessageBox, "warning", lambda *_args: warnings.append(_args))
    panel, context, service = _panel(monkeypatch, (ParaTranzProject(42, "Remote Member"),))

    panel._bind_selected_project()

    assert context.bindings == []
    assert panel._list.isEnabled() is False
    worker = _ControlledWorker.instances[-1]
    worker.complete()

    assert service.closed is True
    assert len(context.bindings) == 1
    assert context.bindings[0].project_name == "Remote Member"
    assert context.bindings[0].validated_at is not None
    assert panel._list.isEnabled() is True
    assert worker.deleted is True
    assert infos and not warnings
    panel.deleteLater()
    qapp.processEvents()


def test_non_member_result_never_writes_binding(qapp, monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(project_panel.QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(project_panel.QMessageBox, "warning", lambda *_args: warnings.append(_args))
    panel, context, service = _panel(monkeypatch, ())

    panel._bind_selected_project()
    _ControlledWorker.instances[-1].complete()

    assert service.closed is True
    assert context.bindings == []
    assert warnings and "不是所选项目的成员" in warnings[-1][2]
    panel.deleteLater()
    qapp.processEvents()
