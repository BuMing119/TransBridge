from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import JobState
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.task_adapter import TerminologyTaskViewState
from transbridge.ui.tools.terminology.view_models import TerminologyArea
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def test_window_close_releases_subscription_and_all_query_models() -> None:
    presenter = TerminologyPresenter(
        TerminologyUiServices(),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)

    window.close()

    assert presenter.closed
    assert all(model.closed for model in window._models)


def test_window_uses_horizontal_object_navigation_without_workflow_tabs() -> None:
    presenter = TerminologyPresenter(
        TerminologyUiServices(),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)

    assert window.workspace.labels == ("概览", "术语", "版本", "报告")
    assert isinstance(window.workspace.layout(), QHBoxLayout)
    assert isinstance(window.workspace.surface.layout(), QVBoxLayout)
    assert window.workspace.navigation.parent() is window.workspace.surface
    assert window.workspace.surface.layout().indexOf(window.workspace.navigation) == 1
    assert window.workspace.surface.layout().indexOf(window.workspace.pages) == 2
    assert not window.findChildren(QWidget, "tbNavigationRail")
    assert not window.findChildren(QTabWidget)
    assert window.workspace.current_area() is TerminologyArea.OVERVIEW
    assert window.draft_model.headerData(0, Qt.Orientation.Horizontal) == "原名"
    assert window.history_model.headerData(0, Qt.Orientation.Horizontal) == "版本"
    assert window.publish_details.isHidden()

    window.workspace.set_current_area(TerminologyArea.VERSIONS)

    assert window.workspace.current_area() is TerminologyArea.VERSIONS
    window.close()


def test_workbench_projects_human_readable_context_into_the_top_project_card() -> None:
    presenter = TerminologyPresenter(
        TerminologyUiServices(),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)

    window.workspace.set_context("Skyrim SE 汉化项目", "简体中文", 18)

    assert window.workspace.project_name.text() == "Skyrim SE 汉化项目"
    assert window.workspace.project_caption.text() == "简体中文 · 18 个来源"
    assert window.workspace.brand_context.text() == "Skyrim SE 汉化项目"
    window.close()


def test_long_command_preparation_runs_off_the_qt_main_thread() -> None:
    presenter = TerminologyPresenter(
        TerminologyUiServices(),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)
    caller_thread = threading.get_ident()
    executed_on = []

    window._run_command(
        lambda: executed_on.append(threading.get_ident()) or JobRef("job", "operator", "run"),
        "任务已开始",
    )
    deadline = time.monotonic() + 2
    while not window._task_refs and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)

    assert executed_on and executed_on[0] != caller_thread
    assert "run" in window._task_refs
    window.close()


def test_completed_build_refreshes_the_overview_summary() -> None:
    result = SimpleNamespace(
        summary=SimpleNamespace(source_count=3, candidate_count=42, conflict_count=2),
        completeness=SimpleNamespace(value="complete"),
        freshness=SimpleNamespace(value="current"),
        ref=SimpleNamespace(build_key="build-key", content_digest="digest"),
        diagnostics=(),
    )

    class _Commands:
        @staticmethod
        def latest_build_ref(_project_id, _variant_id):
            return None

        @staticmethod
        def latest_build_result(_project_id, _variant_id):
            return result

        @staticmethod
        def active_draft(_context):
            return None

    presenter = TerminologyPresenter(
        TerminologyUiServices(commands=_Commands()),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)
    state = TerminologyTaskViewState(
        "run",
        TerminologyWorkloadType.BUILD,
        JobState.COMPLETED,
        "finalize",
        3,
        3,
        "",
        "任务已完成",
        1,
        1,
    )

    window._on_task_change(state)

    assert window.build_view.result.text() == "从 3 个来源整理出 42 个术语候选。"
    assert "2 组同名异译" in window.build_view.decisions.text()
    window.close()
