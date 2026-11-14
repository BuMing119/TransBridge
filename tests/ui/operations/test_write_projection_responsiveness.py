from dataclasses import replace
from threading import Event, get_ident
from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtTest import QTest
import pytest

from tests.ui.operations.test_export_json_dialog import _finish_export
from tests.ui.operations.test_production_facade import _Clock, _Ids
from tests.ui.operations.test_strict_write_routes import _context, _texts, qapp as qapp_fixture
from tests.ui.operations.test_write_schemes import _schemes
from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import TaskRuntime
from transbridge.application.terminology_profiles import FrozenProfileWriteProjection
from transbridge.ui.operations.production import build_operation_plan_facade

qapp = qapp_fixture


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("schemes", [False, True])
def test_confirm_keeps_projection_off_gui_and_reuses_confirmed_translation(tmp_path, qapp, monkeypatch, batch, schemes):
    context = _context(tmp_path, 2 if batch else 1)
    source = _schemes()
    projection = source.resolve((source.choices[0][0],))[0]
    started, release = Event(), Event()
    gui_thread = get_ident()
    project_entries = FrozenProfileWriteProjection.project_entries

    def guarded_project(frozen, entries):
        if get_ident() == gui_thread:
            raise RuntimeError("Terminology projection blocked the GUI thread")
        started.set()
        if not release.wait(5):
            raise RuntimeError("Test did not release background projection")
        return project_entries(frozen, entries)

    monkeypatch.setattr(FrozenProfileWriteProjection, "project_entries", guarded_project)
    tasks = TaskRuntime(id_generator=_Ids(), clock=_Clock())
    facade = build_operation_plan_facade(SimpleNamespace(tasks=tasks), RequestContext("gui"))
    values = {"entry_projection": projection}
    if schemes:
        values["export_schemes"] = replace(source, baseline=projection)
    dialog = facade.begin_write(context, batch=batch, **values)
    output = tmp_path / "output"
    dialog._field_edits["output_directory"].setText(str(output))
    heartbeat = []
    try:
        dialog._confirm_button.click()
        assert started.wait(3)
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qapp.processEvents()
        assert heartbeat and dialog._preflight_running
        release.set()
        _finish_export(dialog, qapp)
        paths = sorted(output.glob("*.esp"))
        assert len(paths) == (2 if batch else 1)
        assert all(_texts(path) == ["甲译名", "译文1"] for path in paths)
        assert next(iter(context.collection)).translation == "译文0"
    finally:
        release.set()
        dialog.set_execution_running(False)
        dialog.close()
        QTest.qWait(20)
