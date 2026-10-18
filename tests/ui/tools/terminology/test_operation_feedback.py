from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.tasks import JobState
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.ui.tools.terminology.asset_refresh import AssetRefreshResult
from transbridge.ui.tools.terminology.asset_snapshot import WorkbenchAssets
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.task_adapter import TerminologyTaskViewState
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def _window():
    return TerminologyWindow(
        TerminologyPresenter(
            TerminologyUiServices(), RequestContext("operator", project_id="project", variant_id="variant")
        )
    )


def test_command_failure_is_visible_on_the_current_terms_page():
    window = _window()
    window._show_command_error(RuntimeError("草稿在检查后已被其他操作修改"))
    assert not window.operation_notice.isHidden()
    assert "草稿在检查后已被其他操作修改" in window.operation_notice.message.text()
    assert window.workspace.isAncestorOf(window.operation_notice)
    window.close()


def test_publish_completion_refreshes_version_but_stale_result_is_not_success():
    window = _window()
    assets = WorkbenchAssets(
        version_ref=SimpleNamespace(version_id="published-2"),
        task_result=SimpleNamespace(freshness="current", committed=True),
    )
    window._apply_assets(assets, TerminologyWorkloadType.PUBLISH)
    assert window.versions_view.current_version.text() == "版本 published-2"
    assert "新术语版本已发布" in window.operation_notice.message.text()
    assets = WorkbenchAssets(
        version_ref=SimpleNamespace(version_id="published-2"),
        task_result=SimpleNamespace(freshness="stale", committed=False, diagnostics=("EXPECTED_STATE_STALE",)),
    )
    window._apply_assets(assets, TerminologyWorkloadType.PUBLISH)
    assert "不能发布" in window.operation_notice.message.text()
    assert "新术语版本已发布" not in window.operation_notice.message.text()
    window.close()


def test_fast_terminal_event_cannot_be_overwritten_by_late_submission_acknowledgement():
    window = _window()
    state = TerminologyTaskViewState(
        "run", TerminologyWorkloadType.BUILD, JobState.CANCELLED, "finalize", 0, 0, "", "已停止", 1, 1
    )
    window._on_task_change(state)
    window._command_ready(JobRef("job", "operator", "run"), "任务已开始")
    assert not window._task_refs
    assert window.operation_notice.message.text() == "来源整理：已停止"
    window.close()


def test_coalesced_assets_keep_build_publish_comparison_and_both_report_feedback(monkeypatch):
    window = _window()
    calls = []
    monkeypatch.setattr(window._browsing, "refresh", lambda: calls.append("shared"))
    summary = SimpleNamespace(title="build-summary", is_partial=False, is_stale=False)
    monkeypatch.setattr(window.presenter, "project_build", lambda _value: summary)
    monkeypatch.setattr(window.build_view, "set_summary", lambda value: calls.append(value.title))
    monkeypatch.setattr(window.history_view, "set_comparison", lambda value: calls.append(value))
    monkeypatch.setattr(window.reports_view, "set_artifact", lambda value: calls.append(value))
    window._profile_controller = SimpleNamespace(refresh=lambda: calls.append("profile-refresh"))
    assets = WorkbenchAssets(
        build=SimpleNamespace(completeness="full", freshness="current"),
        comparison="comparison",
        version_ref=SimpleNamespace(version_id="v2"),
    )
    completions = tuple(
        (kind, SimpleNamespace(freshness="current", committed=True, output_ref=output))
        for kind, output in (
            (TerminologyWorkloadType.BUILD, None),
            (TerminologyWorkloadType.PUBLISH, None),
            (TerminologyWorkloadType.HISTORY_COMPARE, None),
            (TerminologyWorkloadType.REPORT_RENDER, "quality.html"),
            (TerminologyWorkloadType.CHANGELOG_RENDER, "changes.md"),
        )
    )
    try:
        window._asset_view.apply_refresh(AssetRefreshResult(assets, completions))
        assert calls == ["build-summary", "shared", "profile-refresh", "comparison", "quality.html", "changes.md"]
        assert window.versions_view.current_version.text() == "版本 v2"
        assert "changes.md" in window.operation_notice.message.text()
    finally:
        window.close()
