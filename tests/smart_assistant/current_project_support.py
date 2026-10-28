"""Bind unittest assistant cases to current project mutation services."""

from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from tests.ui.fixtures.current_editing import CurrentEditingProject


def bind_current_project(case, entries):
    case.qt_app = QApplication.instance() or QApplication([])
    config = patch(
        "transbridge.paratranz.config_manager.ParatranzConfig.create_or_load",
        return_value=SimpleNamespace(token="test-token", user_id=None),
    )
    config.start()
    case.addCleanup(config.stop)
    project = CurrentEditingProject(entries)
    case.addCleanup(project.close)
    case.project = project
    case.ctx = project.context
    case.collection = project.context.collection
    return project.context
