from PyQt6.QtWidgets import QApplication

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import JobState
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.task_adapter import TerminologyTaskViewState
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def _progress(run, kind, state=JobState.RUNNING):
    return TerminologyTaskViewState(run, kind, state, "collect", 1, 3, "", "执行中", 1, 1)


def _window(monkeypatch):
    presenter = TerminologyPresenter(
        TerminologyUiServices(), RequestContext("operator", project_id="project", variant_id="variant")
    )
    cancelled = []
    monkeypatch.setattr(presenter, "cancel_task", cancelled.append)
    return TerminologyWindow(presenter), cancelled


def test_stop_targets_the_displayed_task_instead_of_the_latest_submission(monkeypatch):
    window, cancelled = _window(monkeypatch)
    build, report = JobRef("build", "operator", "build-run"), JobRef("report", "operator", "report-run")
    try:
        window._command_ready(build, "build")
        window._command_ready(report, "report")
        window._on_task_change(_progress("build-run", TerminologyWorkloadType.BUILD))
        assert "来源整理" in window.build_view.progress_message.text()
        window.build_view.cancel_button.click()
        assert cancelled == [build]
        window._on_task_change(_progress("report-run", TerminologyWorkloadType.REPORT_RENDER))
        assert "质量报告生成" in window.build_view.progress_message.text()
        window.build_view.cancel_button.click()
        assert cancelled == [build, report]
    finally:
        window.close()


def test_progress_before_acknowledgement_never_cancels_a_different_task(monkeypatch):
    window, cancelled = _window(monkeypatch)
    build, report = JobRef("build", "operator", "build-run"), JobRef("report", "operator", "report-run")
    try:
        window._command_ready(report, "report")
        window._on_task_change(_progress("build-run", TerminologyWorkloadType.BUILD))
        assert not window.build_view.cancel_button.isEnabled()
        window.build_view.cancel_button.click()
        assert not cancelled
        window._command_ready(build, "build")
        assert window.build_view.cancel_button.isEnabled()
        window.build_view.cancel_button.click()
        assert cancelled == [build]
    finally:
        window.close()


def test_finished_display_and_late_events_never_fall_back_to_another_task(monkeypatch):
    window, cancelled = _window(monkeypatch)
    try:
        window._command_ready(JobRef("report", "operator", "report-run"), "report")
        window._on_task_change(_progress("build-run", TerminologyWorkloadType.BUILD, JobState.CANCELLED))
        window._command_ready(JobRef("build", "operator", "build-run"), "build")
        window._on_task_change(_progress("build-run", TerminologyWorkloadType.BUILD))
        assert not window.build_view.cancel_button.isEnabled()
        window.build_view.cancel_button.click()
        assert not cancelled
    finally:
        window.close()
