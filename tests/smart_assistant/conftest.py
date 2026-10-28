"""Current project command fixtures for assistant mutation integration tests."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.ui.fixtures.current_editing import CurrentEditingProject


@pytest.fixture
def current_project_factory(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.create_or_load",
        lambda: SimpleNamespace(token="test-token", user_id=None),
    )
    projects = []

    def build(entries, **options):
        project = CurrentEditingProject(entries, **options)
        projects.append(project)
        return project

    yield build
    app.processEvents()
    for project in reversed(projects):
        project.close()
