from dataclasses import replace
import time
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.terminology.browse_queries import TermBrowseStatus, TermFilter
from transbridge.application.terminology.models import ScopeKind, TermScope
from transbridge.application.terminology_profiles import (
    ProfileTermMapping,
    TerminologyProfileContent,
    TerminologyProfileService,
)
from transbridge.bootstrap import build_runtime
from transbridge.ui.tools.terminology.asset_snapshot import WorkbenchAssets
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow
from transbridge.ui.tools.terminology.workflow_state import TerminologyWorkflowState

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 4
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)
    assert predicate()


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    app = build_runtime(settings={"persistence_v2_root": tmp_path})
    composition = app.use_cases.resolve("terminology_ui_services_factory")
    monkeypatch.setattr(type(composition), "sync_service_for", lambda *args: None)
    context = app.context("operator", project_id="project-1", variant_id="main")
    services = TerminologyUiServices.from_runtime(app, context)
    harness = _Harness(services.queries, monkeypatch)
    services = replace(services, commands=harness.commands, build_inputs=harness)
    window = TerminologyWindow(TerminologyPresenter(services, harness.context))
    window._preflight_started = True
    try:
        yield window, harness
    finally:
        window.close()
        window._actions._pool.waitForDone(3000)
        _APP.processEvents()
        app.close()


def test_first_use_explains_empty_result_and_hides_inapplicable_paging(workbench):
    window, _harness = workbench
    window._bind_existing_assets()
    _wait(lambda: window.effective_model.pagination.total == 0)
    assert "还没有项目术语" in window.effective_view.empty.text()
    assert not window.effective_view.empty.isHidden()
    assert window.effective_view.pagination.isHidden()
    assert window.draft_view.add_button.isHidden()
    assert window.terms_view.workflow.publish_button.isHidden()
    assert window.effective_view.empty_action.text() == "从来源生成草稿"


def test_published_content_can_continue_editing_from_the_main_page(workbench):
    window, harness = workbench
    harness.build()
    version = harness.publish()
    window._bind_existing_assets()
    _wait(lambda: window.effective_model.rowCount() == 1 and window._workflow.state is not None)
    assert not window.terms_view.workflow.edit_button.isHidden()
    window.terms_view.workflow.edit_button.click()
    _wait(lambda: harness.repository.active_ref("project-1", "main") is not None)
    _wait(lambda: window.draft_model.rowCount() == 1)
    assert window.terms_view.pages.currentWidget() is window.draft_view
    assert window._workflow.state.has_draft
    assert window.terms_view.workflow.publish_button.isEnabled()
    assert harness.repository.effective_version("project-1", "main").ref == version.ref


def test_filtered_empty_state_preserves_search_and_explains_no_matches(workbench):
    window, harness = workbench
    harness.build()
    harness.publish()
    window._bind_existing_assets()
    _wait(lambda: window.effective_model.rowCount() == 1)
    window.effective_view.filters.search.setText("no such term")
    window.effective_view.filters.apply()
    _wait(lambda: window.effective_model.pagination.total == 0)
    assert "筛选条件" in window.effective_view.empty.text()
    assert not window.effective_view.filters.isHidden()
    assert window.effective_view.empty_action.isHidden()


@pytest.mark.parametrize("completeness,freshness", [("partial", "current"), ("full", "stale")])
def test_incomplete_or_stale_build_is_not_presented_as_publish_ready(completeness, freshness):
    state = TerminologyWorkflowState.from_assets(
        WorkbenchAssets(
            draft=SimpleNamespace(decisions=()),
            build=SimpleNamespace(completeness=completeness, freshness=freshness),
        )
    )
    assert not state.can_publish
    assert state.title == "需要重新整理来源"


def test_later_success_does_not_erase_failed_write_summary(workbench):
    window, _harness = workbench

    def fail():
        raise RuntimeError("草稿版本已变化")

    window._run_draft_command(fail)
    window._run_draft_command(lambda: "saved")
    _wait(lambda: window._draft_writes.pending_count == 0)
    assert "1 项修改未保存" in window.operation_notice.message.text()
    assert "草稿版本已变化" in window.operation_notice.message.text()


@pytest.mark.parametrize("previous_filter", ["search", "scope", "both"])
def test_review_entry_resets_unrelated_filters_and_shows_the_actual_pending_term(workbench, previous_filter):
    window, harness = workbench
    harness.parser.entries["source-a"] += (_entry("source-a", "two", "Sword", "剑"),)
    draft = harness.build()
    dragon = next(item for item in draft.decisions if item.original == "Dragon")
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=dragon.term_id, translation="人工译名"
    )
    harness.publish()
    harness.parser.entries["source-a"] = (
        _entry("source-a", "one", "Dragon", "来源新译名"),
        _entry("source-a", "two", "Sword", "剑"),
    )
    harness.change_source()
    harness.build()
    window._bind_existing_assets()
    _wait(lambda: window._workflow.state is not None and window._workflow.state.review_count == 1)
    filters = window.draft_view.filters
    if previous_filter in {"search", "both"}:
        filters.search.setText("Sword")
    if previous_filter in {"scope", "both"}:
        filters.scope.setCurrentIndex(filters.scope.findData(ScopeKind.PLUGIN))
    filters.apply()
    _wait(lambda: not window.draft_model.pagination.loading)
    requested_filters = []
    window.draft_view.query_changed.connect(requested_filters.append)

    window.terms_view.review_button.click()

    assert requested_filters == [TermFilter(status=TermBrowseStatus.REVIEW_REQUIRED)]
    assert filters.search.text() == ""
    assert filters.scope.currentData() is None
    assert window.terms_view.pages.currentWidget() is window.draft_view
    _wait(lambda: not window.draft_model.pagination.loading and window.draft_model.rowCount() == 1)
    assert window.draft_model.index(0, 0).data() == "Dragon"


@pytest.mark.parametrize("entry", ["effective", "mapping", "published_mapping", "unmapped"])
@pytest.mark.parametrize("scope", [TermScope.project(), TermScope.plugin("Dragon.esp")])
def test_scheme_edit_routes_the_selected_scope_for_each_real_row_type(workbench, entry, scope):
    window, harness = workbench
    harness.build()
    harness.commands.apply_decision(
        "add", harness.context, original="Dragon", translation="插件基础译名", scope=TermScope.plugin("Dragon.esp")
    )
    harness.publish()
    mappings = tuple(
        ProfileTermMapping(
            "Dragon", f"方案译名-{item.canonical_key}", scope_kind=item.kind.value, plugin_id=item.plugin_id
        )
        for item in (TermScope.project(), TermScope.plugin("Dragon.esp"))
        if entry != "unmapped" or item != scope
    )
    service = TerminologyProfileService(harness.repository.localization_profiles)
    profile = service.create_with_content("project-1", "测试方案", TerminologyProfileContent(mappings))
    published = service.publish(profile.profile_id, expected_draft_revision=0)
    service.select("project-1", "main", profile.profile_id)
    requests = []
    window._profile_controller = SimpleNamespace(open_manager=lambda **intent: requests.append(intent))
    window._bind_existing_assets()
    _wait(lambda: window._workflow.state is not None and window.terms_view.source_browser.model.rowCount() == 2)

    if entry == "effective":
        view = window.effective_view
        _wait(lambda: window.effective_model.rowCount() == 2)
        row = next(
            index
            for index in range(window.effective_model.rowCount())
            if window.effective_model.index(index, 0).data(Qt.ItemDataRole.UserRole).scope == scope
        )
        view.table.selectRow(row)
        assert not view.scheme_edit_button.isHidden()
        view.scheme_edit_button.click()
    else:
        view = window.terms_view.source_browser
        if entry == "published_mapping":
            view.render(SimpleNamespace(enabled=True, selected_revision=published))
        row = next(
            index
            for index in range(view.filtered.rowCount())
            if (item := view.filtered.index(index, 0).data(Qt.ItemDataRole.UserRole)).scope_kind == scope.kind.value
            and item.plugin_id == scope.plugin_id
        )
        if entry == "unmapped":
            assert view.filtered.index(row, 0).data(Qt.ItemDataRole.UserRole).mapping is None
        view.table.selectRow(row)
        assert view.edit_button.isEnabled()
        view.edit_button.click()

    assert requests == [{"original": "Dragon", "scope": scope}]
