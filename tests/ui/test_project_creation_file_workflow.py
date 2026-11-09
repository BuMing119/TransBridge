"""Create through the actual start-center form, workers and project installation."""

import time
from types import SimpleNamespace
from uuid import uuid4

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.contracts.io.test_open_translation_file import source_file
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase
from transbridge.ui.shell.start_center import StartCenterWidget
from transbridge.ui.shell.start_center_controller import StartCenterController
from transbridge.ui.workers import ApiWorker

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def creation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token="", base_url="")
    )
    services = build_persistence_v2_services(
        tmp_path / "data", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    request = RequestContext("gui", run_id=uuid4().hex)
    host = QWidget()
    host.project_commands = services.gui_project_commands
    host.runtime_context = request
    host.context = AppContext(
        project_projection=services.project_projection, project_commands=host.project_commands, runtime_context=request
    )
    host.app_runtime = SimpleNamespace(use_cases=SimpleNamespace(resolve=lambda _: services.project_projection))
    host.mode_tabs = SimpleNamespace(setCurrentWidget=lambda _: None, setCurrentIndex=lambda _: None)
    host.messages, host.workers = [], []
    host.show_message = host.messages.append

    def dispatch(operation, *, on_result, on_error, before_start=None, **_):
        if before_start:
            before_start()
        worker = ApiWorker(operation)
        worker.result.connect(on_result)
        worker.error.connect(on_error)
        host.workers.append(worker)
        worker.start()
        return True

    host.start_foreground_task = dispatch
    view = StartCenterWidget(host)
    controller = StartCenterController(host, view)
    controller.start()
    yield services, host, view, controller
    for worker in host.workers:
        worker.wait(5000)
    _APP.processEvents()
    host.context.close_projection()
    host.close()
    services.close()


@pytest.mark.parametrize("kind", ["plugin.sse", "binary.eet", "json.paratranz"])
def test_form_creates_and_installs_real_source_once(creation, tmp_path, kind):
    services, host, view, controller = creation
    path = source_file(tmp_path, kind)
    controller.choose_source_path(str(path))
    view._draft_primary.click()
    view._draft_primary.click()
    assert len(host.workers) == 1
    deadline = time.monotonic() + 5
    while controller.guided_project.state.in_flight and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    state = controller.guided_project.state
    assert state.phase is GuidedDraftPhase.COMPLETED, (state.diagnostic_message, host.messages)
    assert len(host.workers) == 2  # one prepare plus one atomic commit/install
    assert len(host.context.slots) == 1
    assert host.context.active_slot.format_id.value == kind
    assert not host.context.authoritative_projection_diverged()
    assert services.project_lifecycle.active is not None
