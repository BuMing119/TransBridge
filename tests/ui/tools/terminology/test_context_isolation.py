from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
import pytest

from tests.ui.workbench.terminology_async_support import wait_profile
from transbridge.application.contracts import RequestContext
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileService,
)
from transbridge.ui.tools.terminology.window import TerminologyLauncher
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBar
from transbridge.ui.workbench.terminology_profile_controller import TerminologyProfileUiController

_APP = QApplication.instance() or QApplication([])


class _Context(QObject):
    project_changed = pyqtSignal()
    variant_changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.active_version_identity = ("project-a", "variant-a")

    def switch(self, identity) -> None:
        previous = self.active_version_identity
        self.active_version_identity = identity
        if identity is None or previous[0] != identity[0]:
            self.project_changed.emit()
        else:
            self.variant_changed.emit(identity[1])


class _Preview(QWidget):
    def set_terminology_profile(self, _profile, *, base_snapshot=None) -> None:
        pass


@pytest.mark.parametrize("next_identity", [("project-b", "variant-b"), ("project-a", "variant-b"), None])
def test_visible_window_invalidates_immediately_and_reopen_uses_one_context(monkeypatch, next_identity) -> None:
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    profile_ids = {}
    for project in ("project-a", "project-b"):
        profile = service.create(project, project)
        service.publish(profile.profile_id, expected_draft_revision=0)
        profile_ids[project] = profile.profile_id
    context = _Context()
    bar = TerminologyProfileBar()
    preview = _Preview()
    profiles = TerminologyProfileUiController(
        context, SimpleNamespace(profile_service_for=lambda _project: service), bar, preview
    )
    wait_profile(profiles)
    host = SimpleNamespace(
        app_runtime=SimpleNamespace(),
        runtime_context=RequestContext("operator"),
        context=context,
        workbench=SimpleNamespace(terminology_profile_controller=profiles),
    )
    launcher = TerminologyLauncher(host)
    window = launcher.open()
    assert window is not None
    assert window.isVisible()
    assert window.schemes_view.scheme_combo.itemText(1) == "project-a"
    imports = window._schemes._imports
    imports._expected_identity = ("project-a", "variant-a")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))

    context.switch(next_identity)
    profiles.refresh()

    assert window.presenter.closed
    assert not window.isVisible()
    assert not window.isEnabled()
    assert all(model.closed for model in window._models)
    assert window.schemes_view.scheme_combo.itemText(1) == "project-a"
    # An already queued menu action or source-read completion cannot mutate B.
    window._schemes.select(profile_ids["project-b"])
    window._profile_controller.select(profile_ids["project-b"])
    imports._source_ready(object())
    imports._source_failed("late failure")
    assert warnings == []
    assert service.selected_revision("project-a", "variant-a") is None
    if next_identity is not None:
        wait_profile(profiles)
        assert service.selected_revision(*next_identity) is None
        reopened = launcher.open()
        assert reopened is not window
        assert (reopened.presenter.context.project_id, reopened.presenter.context.variant_id) == next_identity
        assert reopened._profile_controller.identity == next_identity
        reopened.schemes_view.scheme_combo.setCurrentIndex(1)
        wait_profile(profiles)
        assert service.selected_revision(*next_identity).profile_id == profile_ids[next_identity[0]]
        assert service.selected_revision("project-a", "variant-a") is None
    launcher.close()
    bar.close()
    preview.close()
    _APP.processEvents()


def test_profile_manager_closes_when_its_project_context_changes() -> None:
    context = _Context()
    service = TerminologyProfileService(InMemoryTerminologyProfileRepository())
    service.create("project-a", "Names")
    bar = TerminologyProfileBar()
    preview = _Preview()
    profiles = TerminologyProfileUiController(
        context, SimpleNamespace(profile_service_for=lambda _project: service), bar, preview
    )
    wait_profile(profiles)
    profiles.open_manager()
    manager = profiles._manager
    assert manager is not None and manager.isVisible()

    context.switch(("project-b", "variant-b"))

    assert not manager.isVisible()
    assert profiles._manager is None
    bar.close()
    preview.close()
    _APP.processEvents()
