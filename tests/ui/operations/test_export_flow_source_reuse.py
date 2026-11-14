from time import monotonic
from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog, QMessageBox
import pytest

from tests.ui.operations.test_production_facade import _Clock, _Ids
from tests.ui.operations.test_strict_write_routes import _texts, qapp as qapp_fixture
from tests.ui.operations.test_write_schemes import _schemes
from tests.ui.operations.test_write_source_references import project as project_fixture
from transbridge.application.contracts import RequestContext
from transbridge.application.tasks import TaskRuntime
from transbridge.ui.coordinators import write_input_coordinator
from transbridge.ui.operations.production import build_operation_plan_facade
from transbridge.ui.operations.write_schemes import PREFIX

project = project_fixture
qapp = qapp_fixture


def _dialog(project, *, schemes=None):
    gui = project.host.context

    def select(scope, resume, **kwargs):
        write_input_coordinator.select_write_inputs(project.host, scope, resume, **kwargs)

    gui.source_input_coordinator = SimpleNamespace(resolved_keys=lambda keys: keys, select_write_inputs=select)
    tasks = TaskRuntime(id_generator=_Ids(), clock=_Clock())
    facade = build_operation_plan_facade(SimpleNamespace(tasks=tasks), RequestContext("gui"))
    return facade.begin_write(project.scope, batch=True, **({"export_schemes": schemes} if schemes else {}))


def _finish(dialog, project, qapp):
    deadline = monotonic() + 10
    while monotonic() < deadline:
        project.complete_all()
        qapp.processEvents()
        QTest.qWait(10)
        if dialog._checks.property("tbStatusId") == "completed":
            return
    pytest.fail(f"Export did not finish: {dialog._checks.text()}")


@pytest.mark.parametrize("location", ["available", "moved", "renamed"])
def test_reopened_project_locates_once_for_all_scheme_copies(project, tmp_path, monkeypatch, qapp, location):
    source_paths = project.plugins
    relocated = location != "available"
    if relocated:
        folder = tmp_path / "relocated"
        folder.mkdir()
        source_paths = tuple(
            path.rename(folder / ("Renamed.esp" if location == "renamed" and index == 0 else path.name))
            for index, path in enumerate(source_paths)
        )
    by_old_name = {old.name: path for old, path in zip(project.plugins, source_paths, strict=True)}
    originals = {path: path.read_bytes() for path in source_paths}
    prompts = []

    def choose(_parent, title, _directory, _filter):
        prompts.append(title)
        assert relocated
        return str(next(path for name, path in by_old_name.items() if name in title)), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    source = _schemes()
    dialog = _dialog(project, schemes=source)
    root = tmp_path / "exports"
    try:
        dialog._field_edits["scheme_directory"].setText(str(root))
        for key, _name in source.choices:
            dialog._field_edits[PREFIX + key].setChecked(True)
        dialog._confirm_button.click()
        _finish(dialog, project, qapp)

        expected = {root / name / path.name for _key, name in source.choices for path in source_paths}
        assert set(root.rglob("*.esp")) == expected
        assert len(prompts) == int(relocated)
        assert all(
            _texts(target) == _texts(next(p for p in source_paths if p.name == target.name)) for target in expected
        )
        assert all(path.read_bytes() == content for path, content in originals.items())
        assert all(slot.source_snapshot.content is None for slot in project.scope.slots.values())
        assert {info.reference_path for info in project.store.sources()} == {str(path) for path in source_paths}
    finally:
        dialog.set_execution_running(False)
        dialog.close()
        QTest.qWait(20)


def test_reopened_project_original_mode_reuses_binding_after_overwrite_confirmation(project, monkeypatch, qapp):
    prompts, confirmations, bindings = [], [], []

    def choose(*args):
        prompts.append(args)
        pytest.fail("Valid saved references should not ask for input plugins")

    bind = write_input_coordinator.bind_plugin_write_input

    def recorded_bind(*args, input_encoding):
        bindings.append(args[2])
        return bind(*args, input_encoding=input_encoding)

    monkeypatch.setattr(write_input_coordinator, "bind_plugin_write_input", recorded_bind)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)

    def confirm(*args):
        confirmations.append(args)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    dialog = _dialog(project)
    try:
        mode = dialog._field_edits["destination_mode"]
        mode.setCurrentIndex(mode.findData("original"))
        dialog._confirm_button.click()
        _finish(dialog, project, qapp)

        assert not prompts
        assert len(confirmations) == 1
        assert sorted(bindings) == sorted(str(path) for path in project.plugins)
        assert all(_texts(path) == [f"Original {index}"] for index, path in enumerate(project.plugins))
        assert all(any(path.parent.glob(f".{path.name}.*.bak")) for path in project.plugins)
    finally:
        dialog.set_execution_running(False)
        dialog.close()
        QTest.qWait(20)
