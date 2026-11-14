from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog, QWidget

from tests.ui.operations.test_export_flow_source_reuse import _dialog, _finish
from tests.ui.operations.test_production_facade import _runtime
from tests.ui.operations.test_strict_write_routes import _context, qapp as qapp_fixture
from tests.ui.operations.test_write_source_references import project as project_fixture
from transbridge.application.contracts import RequestContext
from transbridge.ui.operations.production import build_operation_plan_facade

qapp = qapp_fixture
project = project_fixture


def test_export_is_owned_window_modal_and_keeps_event_loop_running(tmp_path, qapp):
    owner = QWidget()
    owner.show()
    facade = build_operation_plan_facade(_runtime(), RequestContext("gui"), parent=owner)
    dialog = facade.begin_write(_context(tmp_path))
    try:
        QTest.qWait(20)
        assert dialog.parentWidget() is owner
        assert dialog.windowModality() == Qt.WindowModality.WindowModal
        assert qapp.activeModalWidget() is dialog
        assert dialog.windowHandle().transientParent() is owner.windowHandle()
        assert not dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        QTest.qWait(20)
        assert ticks == [True]
    finally:
        dialog.close()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        owner.close()
    assert facade._coordinator.active_window_count == 0


def test_missing_source_picker_is_owned_by_export_window(project, tmp_path, monkeypatch, qapp):
    folder = tmp_path / "moved"
    folder.mkdir()
    relocated = {path.name: path.rename(folder / path.name) for path in project.plugins}
    picker_owners = []

    def choose(parent, title, _directory, _filter):
        picker_owners.append(parent)
        return str(next(path for name, path in relocated.items() if name in title)), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    dialog = _dialog(project)
    try:
        dialog._field_edits["output_directory"].setText(str(tmp_path / "exports"))
        dialog._confirm_button.click()
        _finish(dialog, project, qapp)
        assert picker_owners == [dialog]
        assert len(tuple((tmp_path / "exports").glob("*.esp"))) == 2
    finally:
        dialog.set_execution_running(False)
        dialog.close()
        QTest.qWait(20)
