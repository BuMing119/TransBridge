from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QWidget

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
    assert view.filtered.rowCount() == 1
    assert view.filtered.index(0, 1).data() == "物品 1199"
    assert view.status.text() == "1 / 1,201 条术语"
    view.scope.setCurrentIndex(2)
    assert view.filtered.rowCount() == 0
    view.search.setText("独有")
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
    assert view.status.text() == "尚未选用术语副本"
    view.close()


def test_source_strip_displays_exact_selected_revision_and_switch_failure() -> None:
    view = TerminologySchemesView()
    state = _state(ProfileTermMapping("Dragon", "巨龙"))
    view.render(state)
    assert view.scheme_combo.currentData() == "source"
    assert view.metadata.text() == "独立副本 · 1 条术语 · 第 2 版"
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


def test_project_build_results_never_displace_the_selected_source_table() -> None:
    build = BuildView()
    view = TermsView(DraftView(QStandardItemModel()), QWidget(), build)
    view.render_source(_state(ProfileTermMapping("Dragon", "巨龙")))
    view.show()
    _APP.processEvents()
    build.show()  # A delayed project preflight/build completion requests visibility.
    assert view.source_browser.isVisible()
    assert not build.isVisible()
    view.all_terms_button.click()
    assert build.isVisible()
    view.render_source(_state(ProfileTermMapping("Dragon", "巨龙")))
    assert view.pages.currentIndex() == 1  # Refreshing the same copy preserves navigation.
    view.close()
