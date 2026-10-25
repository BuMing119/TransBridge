from __future__ import annotations

import os
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog
import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap import build_runtime
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.guided_project_coordinator import GuidedDraftPhase
from transbridge.ui.main_window import MainWindow
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.translation_table import COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


def _until(predicate):
    deadline = monotonic() + 8
    while not predicate() and monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def shared_plugin_window(tmp_path, monkeypatch, request):
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.create_or_load",
        lambda: SimpleNamespace(token="", user_id=None),
    )
    monkeypatch.setattr("transbridge.ui.shell.window_lifecycle.WindowLifecycle.restore_state", lambda _self: None)
    monkeypatch.setattr(
        "transbridge.ui.coordinators.project_coordinator.workspace_path", lambda: tmp_path / "workspace.json"
    )
    plugin = write_plugin(tmp_path / "A.esp", [(0x800, "SharedNPC", "Original")])
    runtime = build_runtime({"persistence_v2_root": tmp_path / "data", "ui_config_path": tmp_path / "config.ini"})
    with_project = getattr(request, "param", True)
    request = RequestContext("gui", run_id="shared-plugin-creation", authorized_roots=(str(tmp_path),))
    services = runtime.use_cases.resolve("persistence_v2")
    commands = services.gui_project_commands
    active = None
    original_variant_path = None
    if with_project:
        created = commands.create_project(
            ProjectProvisioningRequest("A", source=ProjectSourceRequest(str(plugin))), request
        )
        assert created.is_success, created.diagnostics
        active = services.project_lifecycle.active
        key = active.variant.snapshot().entries[0].entry_key
        assert commands.update_entry(key, request, translation="Project A translation", stage=1).is_success
        assert commands.save(request).is_success
        original_variant_path = services.variants.path_for(active.formal_variant_ref)
    app_context = AppContext(
        project_projection=services.project_projection, project_commands=commands, runtime_context=request
    )
    window = MainWindow(app_context=app_context, runtime=runtime, runtime_context=request)
    window.show()
    _until(lambda: window.project_open_worker is None and (not with_project or bool(window.context.collection)))
    window._window_lifecycle.auto_saver.stop()
    try:
        yield SimpleNamespace(
            window=window,
            services=services,
            plugin=plugin,
            root=tmp_path,
            request=request,
            original_project_id=None if active is None else active.project_ref.identity.value,
            original_variant_path=original_variant_path,
        )
    finally:
        _until(
            lambda: (
                window.foreground_worker is None and window.project_open_worker is None and window.save_worker is None
            )
        )
        window.tool_windows.dispose(wait_for_worker=True)
        window.context.close_projection()
        window.status_presenter.close()
        window._window_lifecycle._close_ready = True
        window.close()
        window.deleteLater()
        runtime.close()
        _APP.processEvents()


@pytest.mark.parametrize("rename_before_submit", [False, True])
def test_reusing_active_plugin_opens_draft_and_creates_independent_project(
    shared_plugin_window, monkeypatch, rename_before_submit
):
    case = shared_plugin_window
    window = case.window
    source_bytes = case.plugin.read_bytes()
    saved_variant = Path(case.original_variant_path).read_bytes()
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *_: ([str(case.plugin)], ""))
    window._intent_composition.dispatch(IntentId.PROJECT_CREATE)
    window.start_center.choose_plugin_button.click()
    view = window.start_center
    coordinator = window.start_center_controller.guided_project

    assert window.mode_tabs.widget(window.mode_tabs.currentIndex()) is view
    assert view._pages.currentWidget() is view._draft_page
    assert view._source_label.text() == str(case.plugin)
    assert view._draft_primary.isVisible() and view._draft_primary.isEnabled()
    if not rename_before_submit:
        view._draft_primary.click()
        _until(lambda: not coordinator.state.in_flight and window.foreground_worker is None)
        assert coordinator.state.phase is GuidedDraftPhase.FAILED
        assert coordinator.state.diagnostic_code == "PROJECT_NAME_EXISTS"
        assert view._draft_error.isVisible()
        assert "PROJECT_NAME_EXISTS" in view._draft_error.text()
        assert window.context.active_project_id == case.original_project_id

    view._name_edit.setFocus()
    view._name_edit.selectAll()
    QTest.keyClicks(view._name_edit, "Project B")
    view._draft_primary.click()
    _until(lambda: not coordinator.state.in_flight and window.foreground_worker is None)

    assert coordinator.state.phase is GuidedDraftPhase.COMPLETED, coordinator.state.diagnostic_message
    assert window.mode_tabs.currentIndex() == 0
    assert window.context.active_project_id != case.original_project_id
    assert window.context.project_name == "Project B"
    assert tuple(window.context.slots) == (str(case.plugin),)
    assert next(iter(window.context.collection)).translation == ""
    table = window.workbench.preview._table
    _until(lambda: table.rowCount() == 1)
    assert table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.EditRole) == ""
    assert not window.context.authoritative_projection_diverged()
    saved = []
    assert window.save_current_project_async(on_finished=saved.append)
    _until(lambda: bool(saved))
    assert saved == [True]
    assert case.plugin.read_bytes() == source_bytes
    assert Path(case.original_variant_path).read_bytes() == saved_variant


@pytest.mark.parametrize("surface", ["window", "table", "search"])
def test_dragging_the_active_plugin_repeatedly_shows_review_before_opening_a_new_draft(shared_plugin_window, surface):
    case = shared_plugin_window
    window = case.window
    receiver = {
        "window": window,
        "table": window.workbench.preview._table.viewport(),
        "search": window.workbench.preview._filters_view.search_all,
    }[surface]
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(case.plugin))])
    reviews = []
    timer = QTimer(window)
    timer.setSingleShot(True)

    def review():
        dialog = window._intent_composition._drop_controller.plugin_dialog
        if dialog is not None:
            reviews.append((dialog.isVisible(), dialog.add_button.isEnabled(), dialog.notice.text()))
            if len(reviews) == 1:
                dialog.reject()
            else:
                dialog.new_button.click()

    timer.timeout.connect(review)
    for count in (1, 2):
        args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        enter = QDragEnterEvent(QPoint(5, 5), *args)
        move = QDragMoveEvent(QPoint(5, 5), *args)
        drop = QDropEvent(QPointF(5, 5), *args)
        _APP.sendEvent(receiver, enter)
        assert enter.isAccepted()
        _APP.sendEvent(receiver, move)
        assert move.isAccepted()
        # Interact with the real modal review when its event loop starts.
        timer.start(0)
        try:
            _APP.sendEvent(receiver, drop)
        finally:
            timer.stop()
        assert drop.isAccepted()
        assert len(reviews) == count
        visible, add_enabled, notice = reviews[-1]
        assert visible
        assert not add_enabled
        assert "该插件已在当前项目中" in notice
        assert window.context.active_project_id == case.original_project_id
        assert next(iter(window.context.collection)).translation == "Project A translation"
        if count == 1:
            assert window.mode_tabs.currentIndex() == 0
    view = window.start_center
    assert window.mode_tabs.widget(window.mode_tabs.currentIndex()) is view
    assert view._pages.currentWidget() is view._draft_page
    assert view._source_label.text() == str(case.plugin)
    assert window.start_center_controller.guided_project.state.phase is GuidedDraftPhase.EDITING
