"""Shared startup pointers must not route clicks to another window's workbench."""

from itertools import count
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectCatalogEntry, ProjectCatalogSnapshot, ProjectProvisioningRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.ui.context import AppContext
from transbridge.ui.shell.start_center_directory import read_start_center_directory
from transbridge.ui.shell.start_center_projects import PROJECT_ID_ROLE, StartCenterProjectsPanel

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("closed_window", [None, 0, 1])
def test_shared_catalog_routes_projects_using_each_surviving_window(tmp_path, monkeypatch, closed_window):
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.create_or_load",
        lambda: SimpleNamespace(token="", user_id=None),
    )
    identities = count()
    services = []
    contexts = []
    panels = []
    try:
        for name in ("A", "B"):
            service = build_persistence_v2_services(
                tmp_path,
                id_factory=lambda: f"id-{next(identities)}",
                timestamp_factory=lambda: "2026-11-03T00:00:00+08:00",
            )
            services.append(service)
            result = service.gui_project_commands.create_project(
                ProjectProvisioningRequest(name), RequestContext("gui")
            )
            assert result.is_success, result.diagnostics
            contexts.append(AppContext(project_projection=service.project_projection))

        if closed_window is not None:
            assert services[closed_window].gui_project_commands.save(RequestContext("gui")).is_success
            contexts[closed_window].close_projection()
            services[closed_window].close()

        for index, service in enumerate(services):
            if index == closed_window:
                continue
            context = contexts[index]
            assert context.project_name == ("A", "B")[index]
            registry = SimpleNamespace(
                names=lambda: ("project_catalog",), resolve=lambda _name: service.project_catalog
            )
            projects = read_start_center_directory(registry, None).project_rows(context.active_project_id)
            assert [item.project_key for item in projects if item.active] == [context.active_project_id]

            panel = StartCenterProjectsPanel()
            panels.append(panel)
            panel.render(projects)
            returned = []
            opened = []
            panel.return_to_current_requested.connect(lambda: returned.append(True))
            panel.open_current_requested.connect(opened.append)
            for row in range(panel.project_list.count()):
                item = panel.project_list.item(row)
                panel._activate_project(item)
                if item.data(PROJECT_ID_ROLE) == context.active_project_id:
                    assert returned == [True]
                else:
                    dialog = panel._project_dialog
                    assert dialog is not None
                    dialog.current_window_button.click()
            other = next(item for item in projects if not item.active)
            assert opened == [other.path]
    finally:
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for panel in panels:
            panel.close()
            panel.deleteLater()
        _APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for context in contexts:
            context.close_projection()
        for index, service in enumerate(services):
            if index != closed_window:
                service.close()


def test_empty_window_does_not_treat_last_opened_project_as_its_workbench():
    project = ProjectCatalogEntry(project_id="B", name="B", path="B.json", available=True, active=True)
    catalog = SimpleNamespace(list_projects=lambda: ProjectCatalogSnapshot((project,)))
    registry = SimpleNamespace(names=lambda: ("project_catalog",), resolve=lambda _name: catalog)
    assert not read_start_center_directory(registry, None).project_rows(None)[0].active
