from types import SimpleNamespace
from uuid import uuid4

from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMainWindow, QMenu, QMessageBox
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.bootstrap.runtime import UseCaseRegistry
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.project_transfer_coordinator import ProjectTransferCoordinator
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.widget import WorkbenchWidget


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def host_for(root):
    services = build_persistence_v2_services(root, id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now")
    host = QMainWindow()
    host.runtime_context = RequestContext("gui", run_id=uuid4().hex)
    host.project_commands = services.gui_project_commands
    host.context = AppContext(
        project_projection=services.project_projection,
        project_commands=host.project_commands,
        runtime_context=host.runtime_context,
    )
    host.app_runtime = SimpleNamespace(
        use_cases=UseCaseRegistry({
            "project_snapshots": services.project_snapshots,
            "project_archive": services.project_archive,
        })
    )
    host.messages = []
    host.show_message = host.messages.append

    def start(fn, *, message, on_result=None, **_kwargs):
        result = fn()
        if on_result:
            on_result(result)
        return True

    host.start_foreground_task = start
    host.save_current_project_async = lambda *, on_finished: on_finished(
        host.project_commands.save(host.runtime_context).is_success
    )
    host.project_coordinator = SimpleNamespace(
        open_project_path=lambda path: services.current_project_opener.open_path(path, host.runtime_context)
    )
    return host, services


def test_v2_ui_snapshots_and_archive_roundtrip_without_legacy_setters(qapp, tmp_path, monkeypatch):
    host, services = host_for(tmp_path / "first")
    assert host.project_commands.create_project(ProjectProvisioningRequest("UI工程"), host.runtime_context).is_success
    qapp.processEvents()
    assert host.context.active_project is None and host.context.uses_authoritative_projection
    assert host.project_commands.replace_labels({}, {"saved": {"name": "保存的标签"}}, host.runtime_context).is_success
    qapp.processEvents()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("UI快照", True))
    monkeypatch.setattr(QInputDialog, "getItem", lambda _parent, _title, _label, items, *_args: (items[-1], True))
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    coordinator = ProjectTransferCoordinator(host)
    coordinator.save_snapshot()
    assert len(services.project_snapshots.list(host.runtime_context)) == 1
    assert host.project_commands.replace_labels({}, {"changed": {"name": "后续标签"}}, host.runtime_context).is_success
    coordinator.load_snapshot()
    qapp.processEvents()
    restored = services.project_lifecycle.active.variant.snapshot().to_dto().envelope.data
    assert restored["label_library"] == {"saved": {"name": "保存的标签"}}
    assert host.context.dirty
    assert len(services.project_snapshots.list(host.runtime_context)) == 2
    target = tmp_path / "UI工程.transbridge"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda parent, *_args: (str(target), "") if parent is host else pytest.fail("wrong dialog parent"),
    )
    coordinator.export_transbridge()
    assert target.is_file() and not services.project_lifecycle.active.dirty

    other, imported = host_for(tmp_path / "second")
    ProjectTransferCoordinator(other).import_transbridge(str(target))
    qapp.processEvents()
    assert other.context.active_project_id == host.context.active_project_id
    assert other.context.project_name == "UI工程"
    assert imported.project_lifecycle.active.variant.snapshot().to_dto().envelope.data == restored
    host.close()
    other.close()
    services.close()
    imported.close()


def test_project_bar_snapshot_actions_dispatch_real_shell_intents(qapp, monkeypatch):
    context = AppContext()
    widget = WorkbenchWidget(context)
    intents = []
    widget.intent_requested.connect(intents.append)

    def choose_snapshot_actions(menu, *_args):
        submenu = next(action.menu() for action in menu.actions() if action.text() == "管理快照")
        for action in submenu.actions():
            action.trigger()

    monkeypatch.setattr(QMenu, "exec", choose_snapshot_actions)
    widget.project_bar._on_variant_menu()
    assert intents == [IntentId.PROJECT_SNAPSHOT_SAVE.value, IntentId.PROJECT_SNAPSHOT_LOAD.value]
    widget.close()
