from dataclasses import replace
import time

from PyQt6.QtWidgets import QApplication

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from tests.integration.bootstrap.test_terminology_task_wiring import _create_project_context
from transbridge.application.terminology.browse_queries import TermBrowseStatus
from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent
from transbridge.bootstrap import build_runtime
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def _wait(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert predicate()


def test_real_window_distinguishes_draft_from_complete_actual_profile_mapping(tmp_path, monkeypatch):
    app = build_runtime(settings={"persistence_v2_root": tmp_path})
    window = None
    try:
        composition = app.use_cases.resolve("terminology_ui_services_factory")
        monkeypatch.setattr(type(composition), "sync_service_for", lambda *args: None)
        source = _create_project_context(app, "Terminology browsing")
        project_id = source.project_id
        commands = app.use_cases.resolve("gui_project_commands")
        profiles = composition.profile_service_for(project_id)
        profile = profiles.create_with_content(
            project_id,
            "方案甲",
            TerminologyProfileContent(
                mappings=(
                    ProfileTermMapping("Dragon", "巨龙"),
                    ProfileTermMapping("Missing", "孤立映射"),
                )
            ),
        )
        published = profiles.publish(profile.profile_id, expected_draft_revision=0)
        saved = commands.save_profile_variant(
            "Selected terminology", source, target_profile=published, source_profile=None
        )
        assert saved.is_success, saved.diagnostics
        context = app.context("operator", project_id=project_id, variant_id=saved.value["id"])
        services = TerminologyUiServices.from_runtime(app, context)
        harness = _Harness(services.queries, monkeypatch, project_id=project_id, variant_id=context.variant_id)
        harness.build()
        version = harness.publish()
        draft = harness.build()
        harness.commands.apply_decision(
            "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="草稿新译名"
        )
        services = replace(services, commands=harness.commands)
        window = TerminologyWindow(TerminologyPresenter(services, context))
        window._bind_existing_assets()
        window.terms_view.browse_button.click()
        _wait(lambda: window.effective_model.rowCount() > 0)
        assert window.effective_model.rowCount() == 2
        assert window.terms_view.pages.currentWidget() is window.effective_view
        assert window.effective_model.index(0, 1).data() == "巨龙"
        assert window.effective_model.index(1, 0).data() == "Missing"
        assert window.effective_model.index(1, 1).data() == "孤立映射"
        window.terms_view.show_draft()
        _wait(lambda: window.draft_model.rowCount() == 1)
        assert window.draft_model.index(0, 1).data() == "草稿新译名"
        assert version.ref.version_id in window.workspace.current_version.text()
        browser = window.terms_view.source_browser
        window.terms_view.show_source()
        _wait(lambda: browser.model.rowCount() > 0)
        browser.search.setText("Missing")
        assert browser.filtered.rowCount() == 1
        assert browser.filtered.index(0, 3).data() == "当前生效"
        window.terms_view.review_button.click()
        _wait(lambda: not window.draft_model.is_loading)
        assert window.draft_model.rowCount() == 0
        assert window._browsing.filters["draft"].status is TermBrowseStatus.REVIEW_REQUIRED
        browser.draft_button.click()
        assert window.terms_view.pages.currentWidget() is window.draft_view
        assert profiles.selected_revision(project_id, source.variant_id) is None
    finally:
        if window is not None:
            window.close()
            window._actions._pool.waitForDone(3000)
        app.close()


def test_real_window_full_search_and_refresh_preserve_page_and_filters(tmp_path, monkeypatch):
    app = build_runtime(settings={"persistence_v2_root": tmp_path})
    window = None
    try:
        composition = app.use_cases.resolve("terminology_ui_services_factory")
        monkeypatch.setattr(type(composition), "sync_service_for", lambda *args: None)
        context = _create_project_context(app, "Terminology paging")
        services = TerminologyUiServices.from_runtime(app, context)
        harness = _Harness(services.queries, monkeypatch, project_id=context.project_id, variant_id=context.variant_id)
        harness.parser.entries["source-a"] = tuple(
            _entry("source-a", str(index), f"Item {index:04}", f"物品{index:04}") for index in range(601)
        )
        harness.build()
        services = replace(services, commands=harness.commands)
        window = TerminologyWindow(TerminologyPresenter(services, context))
        window._bind_existing_assets()
        window.terms_view.show_draft()
        _wait(lambda: window.draft_model.rowCount() == 100)
        model = window.draft_model
        for _ in range(5):
            model.navigate_next()
            _wait(lambda: not model.is_loading)
        assert model.index(0, 0).data() == "Item 0500"
        window._bind_existing_assets()
        _wait(lambda: not window._actions._pool.activeThreadCount())
        _APP.processEvents()
        _wait(lambda: not model.is_loading)
        assert model.index(0, 0).data() == "Item 0500"
        filters = window.draft_view.filters
        filters.search.setText("物品0600")
        filters.apply_button.click()
        _wait(lambda: not model.is_loading)
        assert model.pagination.total == 1
        assert model.index(0, 0).data() == "Item 0600"
        window._bind_existing_assets()
        _wait(lambda: not window._actions._pool.activeThreadCount())
        _APP.processEvents()
        _wait(lambda: not model.is_loading)
        assert window._browsing.filters["draft"].search == "物品0600"
        assert model.pagination.total == 1
    finally:
        if window is not None:
            window.close()
            window._actions._pool.waitForDone(3000)
        app.close()
