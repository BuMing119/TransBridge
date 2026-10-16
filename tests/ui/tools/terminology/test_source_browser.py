from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QWidget

from tests.ui.tools.terminology.search_test_support import wait_search
from transbridge.application.terminology_profiles import (
    ProfileTermMapping,
    PublishedTerminologyProfile,
    TerminologyProfileContent,
)
from transbridge.ui.tools.terminology.build_view import BuildView
from transbridge.ui.tools.terminology.draft_view import DraftView
from transbridge.ui.tools.terminology.object_views import TermsView
from transbridge.ui.tools.terminology.schemes_view import TerminologySchemesView
from transbridge.ui.tools.terminology.source_browser import SourceBrowser
from transbridge.ui.workbench.terminology_profile_bar import TerminologyProfileBarState, TerminologyProfileChoice

_APP = QApplication.instance() or QApplication([])


def _state(*mappings: ProfileTermMapping) -> TerminologyProfileBarState:
    content = TerminologyProfileContent(mappings)
    revision = PublishedTerminologyProfile(
        "source", "project", 2, "社区术语", content.content_digest, content, "2026-09-09T00:00:00Z"
    )
    return TerminologyProfileBarState(
        choices=(TerminologyProfileChoice("source", "社区术语"),),
        selected_profile_id="source",
        enabled=True,
        can_manage=True,
        selected_revision=revision,
    )


def test_browser_searches_the_full_published_copy_and_combines_scope_filter() -> None:
    view = SourceBrowser()
    mappings = tuple(ProfileTermMapping(f"Item {i:04}", f"物品 {i}") for i in range(1200))
    view.render(_state(*mappings, ProfileTermMapping("Unique", "独有", scope_kind="plugin", plugin_id="test.esp")))

    assert view.model.rowCount() == 1201
    view.search.setText("iTEM 1199")
    wait_search(view._search)
    assert view.filtered.rowCount() == 1
    assert view.filtered.index(0, 1).data() == "物品 1199"
    assert view.status.text() == "1 / 1,201 条术语"
    view.scope.setCurrentIndex(2)
    wait_search(view._search)
    assert view.filtered.rowCount() == 0
    view.search.setText("独有")
    wait_search(view._search)
    assert view.filtered.rowCount() == 1
    assert view.filtered.index(0, 2).data() == "test.esp"
    assert not view.filtered.flags(view.filtered.index(0, 1)) & Qt.ItemFlag.ItemIsEditable
    view.close()


def test_browser_replaces_and_clears_selected_copy_without_stale_rows() -> None:
    view = SourceBrowser()
    view.render(_state(ProfileTermMapping("Dragon", "巨龙")))
    assert view.filtered.index(0, 1).data() == "巨龙"
    view.render(_state(ProfileTermMapping("Dragon", "神龙")))
    assert view.filtered.index(0, 1).data() == "神龙"
    view.render(TerminologyProfileBarState(enabled=False, detail="读取失败"))
    assert view.model.rowCount() == 0
    assert view.status.text() == "读取失败"
    view.render(TerminologyProfileBarState(enabled=True))
    assert view.model.rowCount() == 0
    assert view.status.text() == "使用项目默认译名，没有方案映射。"
    view.close()


def test_background_source_search_keeps_base_text_scope_status_and_selection():
    rows = (
        SimpleNamespace(
            original="Alpha term",
            translation="甲",
            base_translation="Old default",
            scope_kind="project",
            plugin_id=None,
            state="effective",
        ),
        SimpleNamespace(
            original="Beta term",
            translation="乙",
            base_translation="",
            scope_kind="plugin",
            plugin_id="Beta.esp",
            state="base_missing",
        ),
    )
    view = SourceBrowser()
    view._search._small_limit = 0
    try:
        view.render_context(SimpleNamespace(mapping_rows=rows, profile_id="profile", diagnostics=()))
        view.table.setCurrentIndex(view.filtered.index(1, 0))
        view.search.setText("TERM")
        wait_search(view._search)
        assert view.filtered.rowCount() == 2
        assert view.table.currentIndex().data(Qt.ItemDataRole.UserRole) is rows[1]
        view.search.setText("OLD DEFAULT")
        wait_search(view._search)
        assert view.filtered.rowCount() == 1
        assert view.filtered.index(0, 0).data() == "Alpha term"
        view.state_filter.setCurrentIndex(view.state_filter.findData("base_missing"))
        wait_search(view._search)
        assert view.filtered.rowCount() == 0
        view.search.clear()
        view.scope.setCurrentIndex(view.scope.findData("plugin"))
        wait_search(view._search)
        assert view.filtered.rowCount() == 1
        assert view.filtered.index(0, 0).data() == "Beta term"
        view.search.setText("Beta.esp")
        wait_search(view._search)
        assert view.filtered.rowCount() == 0  # Source search retains its original three text fields.
    finally:
        view.close()


def test_source_strip_displays_exact_selected_revision_and_switch_failure() -> None:
    view = TerminologySchemesView()
    state = _state(ProfileTermMapping("Dragon", "巨龙"))
    view.render(state)
    assert view.scheme_combo.currentData() == "source"
    assert view.metadata.text() == "1 条方案映射 · 已应用修订 2"
    assert view.status_label.isHidden()
    view.render(replace(state, selection_error="切换失败：文件只读"))
    assert not view.status_label.isHidden()
    assert view.status_label.text() == "切换失败：文件只读"
    assert view.scheme_combo.currentData() == "source"
    view.close()


def test_draft_actions_require_a_selected_row_and_follow_its_enabled_state() -> None:
    model = QStandardItemModel()
    decision = SimpleNamespace(suppressed=True)
    item = QStandardItem("Dragon")
    item.setData(decision, Qt.ItemDataRole.UserRole)
    model.appendRow(item)
    view = DraftView(model)
    edits = []
    view.edit_requested.connect(edits.append)
    assert view.row_actions.isHidden()
    view.table.selectRow(0)
    assert not view.row_actions.isHidden()
    assert view.suppress_button.text() == "启用"
    view.edit_button.click()
    assert edits == [decision]
    view.table.clearSelection()
    assert view.row_actions.isHidden()
    view.edit_button.click()
    assert edits == [decision]
    assert not hasattr(view, "publish_button")
    view.close()


def test_profile_refresh_preserves_navigation_and_project_actions_remain_available() -> None:
    build = BuildView()
    view = TermsView(DraftView(QStandardItemModel()), QWidget(), build)
    view.render_source(_state(ProfileTermMapping("Dragon", "巨龙")))
    view.source_button.click()
    view.show()
    _APP.processEvents()
    build.show()  # A delayed project preflight/build completion requests visibility.
    assert view.source_browser.isVisible()
    assert not build.isVisible()
    assert view._project_actions.isVisible()
    view.build_details_button.click()
    assert build.isVisible()
    view.all_terms_button.click()
    assert build.isVisible()
    view.render_source(_state(ProfileTermMapping("Dragon", "巨龙")))
    assert view.pages.currentIndex() == 1  # Refreshing the same copy preserves navigation.
    view.close()
