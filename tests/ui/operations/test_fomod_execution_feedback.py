"""Exercise the real plan/worker/runtime path with local synthetic FOMOD reports."""

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QCheckBox
import pytest

from tests.ui.operations.test_production_facade import _Clock, _Ids
from tests.ui.operations.test_target_plugin_workflow import _wait_until
from transbridge.application.contracts import Diagnostic, DiagnosticSeverity, OperationOutcome, RequestContext
from transbridge.application.fomod import PipelineResult
from transbridge.application.fomod.models import ArtifactRef, FomodStageId, StageResult
from transbridge.application.tasks import CallbackThreadBackend, OwnerRef, TaskRuntime
from transbridge.ui.operations import production
from transbridge.ui.tools.fomod.fomod_panel import FomodPanel


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def fomod_case(tmp_path, qapp, monkeypatch):
    source = tmp_path / "new.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("fomod/info.xml", "<fomod />")
    queued = []
    tasks = TaskRuntime(
        id_generator=_Ids(), clock=_Clock(), backend=CallbackThreadBackend(lambda _, fn: queued.append(fn))
    )
    facade = production.build_operation_plan_facade(SimpleNamespace(tasks=tasks), RequestContext("gui"))
    # Own all synthetic runtime files within pytest's temporary directory.
    import transbridge.application.fomod as fomod

    original_draft = fomod.FomodTaskDraft
    monkeypatch.setattr(
        fomod,
        "FomodTaskDraft",
        lambda *args, **kw: replace(original_draft(*args, **kw), workspace_root=str(tmp_path / "work")),
    )
    panel = FomodPanel(SimpleNamespace(), operation_plan_facade=facade)
    panel.prefill_new_archive(str(source))
    panel.show()
    case = SimpleNamespace(panel=panel, facade=facade, queued=queued, tasks=tasks, root=tmp_path)
    yield case
    _wait_until(lambda: not facade._coordinator._preflight_workers)
    if panel._plan_dialog is not None:
        panel._plan_dialog.set_execution_running(False)
    panel.close()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_cancelling_confirmation_restores_not_started(fomod_case):
    case = fomod_case
    case.panel._run()
    assert "尚未开始" in case.panel._result_text.toPlainText()
    assert not case.panel._run_btn.isEnabled()
    case.panel._plan_dialog.reject()
    assert case.panel._result_text.property("tbStatusId") == "not-run"
    assert "尚未开始" in case.panel._result_text.toPlainText()
    assert case.panel._run_btn.isEnabled()
    assert not case.queued


@pytest.mark.parametrize("outcome", list(OperationOutcome))
def test_parent_and_plan_show_the_same_terminal_result(fomod_case, monkeypatch, outcome):
    case = fomod_case

    def engine_factory(*_args):
        def run(spec, _cancellation):
            diagnostics = (
                ()
                if outcome is OperationOutcome.COMPLETED
                else (
                    Diagnostic(
                        "FOMOD_CHECK_FAILED",
                        "归档检查未完整完成。",
                        severity=DiagnosticSeverity.WARNING
                        if outcome is OperationOutcome.CANCELLED
                        else DiagnosticSeverity.ERROR,
                        details=(("recovery_directory", str(case.root / "recovery")),),
                    ),
                )
            )
            artifacts = ()
            if outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
                Path(spec.output_archive).write_bytes(b"synthetic archive")
                artifacts = (ArtifactRef("published", "published-archive", spec.output_archive),)
            stages = (StageResult(FomodStageId.PUBLISH, outcome, artifacts, diagnostics),)
            return PipelineResult(
                spec.run_id, spec.target_locale, spec.config_hash, outcome, stages, artifacts, diagnostics
            )

        return SimpleNamespace(run=run)

    monkeypatch.setattr(production, "_fomod_engine_factory", lambda _rules: engine_factory)
    case.panel._run()
    dialog = case.panel._plan_dialog
    assert "typed pipeline" not in dialog._summary.text()
    assert "生成 ZIP" in dialog._summary.text()
    assert isinstance(dialog._field_edits["overwrite_confirmed"], QCheckBox)
    dialog._preflight_button.click()
    _wait_until(lambda: not case.facade._coordinator._preflight_workers)
    assert dialog._confirm_button.isEnabled(), dialog._checks.text()
    assert case.panel._result_text.property("tbStatusId") == "waiting"
    dialog._confirm_button.click()
    assert case.panel._result_text.property("tbStatusId") == "running"
    assert not case.panel.close()
    case.queued.pop()()
    _wait_until(lambda: not dialog._execution_running)
    assert dialog._checks.property("tbStatusId") == outcome.value
    assert case.panel._result_text.property("tbStatusId") == outcome.value
    assert case.panel._result_text.toPlainText() == dialog._checks.text()
    if outcome in {OperationOutcome.COMPLETED, OperationOutcome.PARTIAL}:
        assert "new-translated.zip" in dialog._checks.text()
    if outcome is not OperationOutcome.COMPLETED:
        assert "归档检查未完整完成" in dialog._checks.text()
        assert str(case.root / "recovery") in dialog._checks.text()
    job = case.tasks.list(OwnerRef("gui", "gui.operation-plan"))[0]
    assert dict(job.progress)["operation_outcome"] == outcome.value
    dialog.close()
    assert case.panel._run_btn.isEnabled()
    assert case.panel._result_text.property("tbStatusId") == outcome.value


def test_existing_output_requires_explicit_checkbox(fomod_case):
    case = fomod_case
    output = Path(case.panel._out_edit.text())
    output.write_bytes(b"previous")
    case.panel._run()
    dialog = case.panel._plan_dialog
    dialog._preflight_button.click()
    _wait_until(lambda: not case.facade._coordinator._preflight_workers)
    assert not dialog._confirm_button.isEnabled()
    assert "输出文件已存在" in dialog._checks.text()
    assert "FOMOD_OVERWRITE" not in dialog._checks.text()
    dialog._edit_button.click()
    dialog._field_edits["overwrite_confirmed"].setChecked(True)
    dialog._preflight_button.click()
    _wait_until(lambda: not case.facade._coordinator._preflight_workers)
    assert dialog._confirm_button.isEnabled()
    assert output.read_bytes() == b"previous"
