from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import JobState
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.ui.tools.terminology.build_view import BuildView
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.task_adapter import TerminologyTaskViewState
from transbridge.ui.tools.terminology.view_models import (
    TerminologyArea,
    TerminologyPreflightViewState,
    TerminologySummaryViewState,
)
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


def test_window_mounts_sync_as_a_versions_child_without_expanding_window_workflow() -> None:
    presenter = TerminologyPresenter(
        TerminologyUiServices(sync=object()),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )

    window = TerminologyWindow(presenter)

    assert window.versions_view._sync_panel is window.sync_view
    assert window.versions_view.isAncestorOf(window.sync_view)
    assert window.sync_view.backup_button.text() == "备份已发布版本…"
    assert window.sync_view.bidirectional_button.text() == "双向同步…"
    window.close()


def test_versions_page_scrolls_instead_of_clipping_on_a_short_screen() -> None:
    presenter = TerminologyPresenter(
        TerminologyUiServices(sync=object()),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)
    window.resize(window.minimumWidth(), window.minimumHeight())
    window.workspace.set_current_area(TerminologyArea.VERSIONS)
    window.show()
    _APP.processEvents()

    assert window.versions_view.scroll.verticalScrollBar().maximum() > 0
    assert window.versions_view.scroll.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    window.close()


def test_window_uses_horizontal_object_navigation_without_workflow_tabs() -> None:
    presenter = TerminologyPresenter(
        TerminologyUiServices(),
        RequestContext("operator", project_id="project", variant_id="variant"),
    )
    window = TerminologyWindow(presenter)

    assert window.workspace.labels == ("术语", "版本", "报告")
    assert isinstance(window.workspace.layout(), QHBoxLayout)
    assert isinstance(window.workspace.surface.layout(), QVBoxLayout)
    assert window.workspace.navigation.parent().objectName() == "terminologyHeader"
    assert window.workspace.surface.layout().indexOf(window.workspace.pages) == 1
    assert not window.findChildren(QWidget, "tbNavigationRail")
    assert not window.findChildren(QTabWidget)
    assert window.findChild(QLabel, "terminologyBrandMark") is None
    assert window.workspace.current_area() is TerminologyArea.TERMS
    assert window.terms_view.isAncestorOf(window.build_view)
    assert window.draft_model.headerData(0, Qt.Orientation.Horizontal) == "原名"
    assert window.history_model.headerData(0, Qt.Orientation.Horizontal) == "版本"
    assert window.publish_details.isHidden()

    window.workspace.set_current_area(TerminologyArea.VERSIONS)

    assert window.workspace.current_area() is TerminologyArea.VERSIONS
    window.workspace.set_current_area(TerminologyArea.SCHEMES)
    assert window.workspace.current_area() is TerminologyArea.TERMS
    window.workspace.set_current_area(TerminologyArea.OVERVIEW)
    assert window.workspace.current_area() is TerminologyArea.TERMS
    assert window.workspace.surface.isAncestorOf(window.schemes_view)
    assert window.schemes_view.actions_button.text() == "选用术语源…"
    assert window.schemes_view.create_action.text() == "从术语源导入副本…"
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
    assert window.workspace.project_caption.isHidden()
    assert window.workspace.brand_context.isHidden()
    assert window.workspace.project_name.accessibleDescription() == "简体中文，18 个来源"
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
    assert window.build_view.result_title.text() == "待处理"
    window.close()


def test_overview_hides_empty_and_technical_sections_until_they_are_useful() -> None:
    view = BuildView()

    assert view.isHidden()
    assert view.alert.isHidden()
    assert view.metrics.isHidden()
    assert view.result_group.isHidden()
    assert view.preflight_button.isHidden()
    assert not hasattr(view, "llm_enabled")

    view.set_preflight(
        TerminologyPreflightViewState(
            ready=True,
            title="可以开始整理",
            message="当前项目已准备好。",
            action_label="开始整理",
        )
    )

    assert view.alert.isHidden()
    assert view.preflight_button.isHidden()
    assert view.build_button.text() == "开始整理"
    assert view.isHidden()

    view.set_summary(
        TerminologySummaryViewState(
            title="整理完成",
            result="从 1 个来源整理出 8 个术语。",
            decisions="当前没有不同译法。",
            impact="不会自动修改项目译文。",
            next_action="可以查看术语。",
            term_count=8,
            attention_count=0,
        )
    )

    assert not view.metrics.isHidden()
    assert not view.result_group.isHidden()
    assert not view.isHidden()
    assert view.result_title.text() == "已完成"
    assert view.decisions.isHidden()
    assert view.impact.isHidden()
    assert view.next_action.isHidden()

    view.set_preflight(TerminologyPreflightViewState.unavailable("缺少可用的术语来源。"))

    assert not view.alert.isHidden()
    assert not view.preflight_button.isHidden()
    view.close()
