"""Project group navigation never changes the workbench execution scope."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.ui.tools.smart_assistant.session_list_widget import SessionListWidget


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sidebar(qapp):
    widget = SessionListWidget()
    widget.set_workbench("a", "a-main")
    widget.set_sessions([
        {
            "session_id": "b-terms",
            "name": "城镇术语检查",
            "project_id": "b",
            "project_name": "城镇扩展",
            "variant_id": "b-main",
            "variant_name": "正式版",
        },
        {
            "session_id": "a-main",
            "name": "地名翻译",
            "project_id": "a",
            "project_name": "主线工程",
            "variant_id": "a-main",
            "variant_name": "稳定版",
        },
        {
            "session_id": "a-preview",
            "name": "增量校对",
            "project_id": "a",
            "project_name": "主线工程",
            "variant_id": "preview-variant-id",
        },
        {"session_id": "legacy", "name": "历史会话"},
    ])
    widget.resize(260, 700)
    widget.show()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qapp.processEvents()


def test_current_project_expands_with_variants_and_others_remain_browsable(sidebar):
    assert list(sidebar._groups) == ["a", "b", None]
    assert sidebar._groups["a"].expanded
    assert not sidebar._groups["b"].expanded
    assert not sidebar._groups[None].expanded
    assert sidebar._groups[None].name == "未归属对话"
    assert "当前工程" in sidebar._groups["a"].header.accessibleName()
    assert "当前工程" not in sidebar._groups["b"].header.accessibleName()
    assert sidebar._rows["a-main"].isVisible()
    assert sidebar._rows["a-preview"].isVisible()
    assert not sidebar._rows["b-terms"].isVisible()
    assert sidebar._rows["b-terms"].isEnabled()
    assert sidebar._rows["a-main"]._variant_label.full_text == "版本：稳定版"
    assert sidebar._rows["a-preview"]._variant_label.full_text == "版本：preview-variant-id"
    assert not sidebar._rows["a-main"].property("outsideWorkbench")
    assert sidebar._rows["a-preview"].property("outsideWorkbench")
    assert sidebar._rows["b-terms"].property("outsideWorkbench")


def test_header_only_expands_and_selecting_foreign_conversation_only_browses(sidebar, qapp):
    switched = []
    sidebar.switch_session.connect(switched.append)
    sidebar.switch_session.connect(sidebar.set_active)
    sidebar._groups["b"].header.click()
    qapp.processEvents()
    assert sidebar._rows["b-terms"].isVisible()
    assert switched == []
    QTest.mouseClick(sidebar._rows["b-terms"], Qt.MouseButton.LeftButton)
    assert switched == ["b-terms"]
    assert sidebar._active_sid == "b-terms"
    assert sidebar._rows["b-terms"].property("active")
    assert sidebar._rows["b-terms"].property("outsideWorkbench")
    assert "仅浏览" in sidebar._rows["b-terms"].accessibleDescription()
    assert sidebar._workbench_project_id == "a"
    assert sidebar._workbench_variant_id == "a-main"


@pytest.mark.parametrize("query", ["城镇术语", "城镇扩展", "正式版", "b-main"])
def test_search_reveals_matching_sessions_in_collapsed_groups_and_restores_state(sidebar, qapp, query):
    sidebar._search_btn.click()
    sidebar._search_input.setText(query)
    qapp.processEvents()
    assert list(sidebar._rows) == ["b-terms"]
    assert sidebar._groups["b"].expanded
    assert sidebar._rows["b-terms"].isVisible()
    sidebar._search_input.clear()
    qapp.processEvents()
    assert not sidebar._groups["b"].expanded
    assert sidebar._groups["a"].expanded


def test_keyboard_skips_collapsed_rows_and_can_expand_group_without_switch(sidebar, qapp):
    switched = []
    sidebar.switch_session.connect(switched.append)
    sidebar._groups[None].header.click()
    qapp.processEvents()
    row = sidebar._rows["a-preview"]
    row.setFocus()
    QTest.keyClick(row, Qt.Key.Key_Down)
    assert sidebar._rows["legacy"].hasFocus()
    header = sidebar._groups["b"].header
    header.setFocus()
    QTest.keyClick(header, Qt.Key.Key_Space)
    assert sidebar._groups["b"].expanded
    assert switched == []
    sidebar._rows["a-preview"].setFocus()
    QTest.keyClick(sidebar._rows["a-preview"], Qt.Key.Key_Down)
    assert sidebar._rows["b-terms"].hasFocus()


def test_refresh_preserves_expansion_and_focus_without_changing_browsed_session(sidebar, qapp):
    sidebar._groups["b"].header.click()
    sidebar.set_active("b-terms")
    sidebar._rows["b-terms"]._menu_btn.setFocus()
    assert sidebar._rows["b-terms"]._menu_btn.hasFocus()
    sidebar.set_sessions(sidebar._session_data)
    qapp.processEvents()
    assert sidebar._groups["b"].expanded
    assert sidebar._rows["b-terms"]._menu_btn.hasFocus()
    sidebar.set_workbench("a", "preview-variant-id")
    qapp.processEvents()
    assert sidebar._active_sid == "b-terms"
    assert not sidebar._rows["a-preview"].property("outsideWorkbench")
    assert sidebar._rows["a-main"].property("outsideWorkbench")
    assert sidebar._rows["b-terms"].property("outsideWorkbench")
    sidebar.set_workbench("b", "b-main")
    qapp.processEvents()
    assert sidebar._groups["b"].expanded
    assert not sidebar._rows["b-terms"].property("outsideWorkbench")
    assert sidebar._rows["b-terms"].property("active")


def test_collapsed_sidebar_retains_group_expansion_and_header_focus_on_refresh(sidebar, qapp):
    sidebar._groups["b"].header.click()
    sidebar._groups["b"].header.setFocus()
    assert sidebar._groups["b"].header.hasFocus()
    sidebar.set_sessions(sidebar._session_data)
    qapp.processEvents()
    assert sidebar._groups["b"].header.hasFocus()
    sidebar.set_collapsed(True)
    assert sidebar.maximumWidth() == SessionListWidget.COMPACT_WIDTH
    sidebar.set_collapsed(False)
    assert sidebar._groups["b"].expanded
    assert sidebar._rows["b-terms"].isVisible()
