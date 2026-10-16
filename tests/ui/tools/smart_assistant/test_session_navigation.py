from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox
import pytest

from transbridge.ui.tools.smart_assistant.session_list_widget import SessionListWidget


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _sessions(qapp):
    widget = SessionListWidget()
    widget.set_sessions([
        {"session_id": "first", "name": "地名翻译"},
        {"session_id": "second", "name": "检查术语一致性"},
    ])
    widget.show()
    qapp.processEvents()
    return widget


def test_closing_search_restores_all_sessions_and_new_session_is_not_filtered(qapp):
    widget = _sessions(qapp)
    widget._search_btn.click()
    widget._search_input.setText("术语")
    assert list(widget._rows) == ["second"]
    widget._search_btn.click()
    assert widget._search_input.isHidden()
    assert list(widget._rows) == ["first", "second"]

    widget._search_btn.click()
    widget._search_input.setText("术语")
    widget.create_session.connect(
        lambda name: widget.set_sessions([*widget._session_data, {"session_id": "new", "name": name}])
    )
    widget._add_btn.click()
    assert "new" in widget._rows
    widget.close()


def test_keyboard_can_switch_rename_delete_and_reach_menu(qapp, monkeypatch):
    widget = _sessions(qapp)
    switched, renamed, deleted = [], [], []
    widget.switch_session.connect(switched.append)
    widget.rename_session.connect(lambda sid, name: renamed.append((sid, name)))
    widget.delete_session.connect(deleted.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    first = widget._rows["first"]
    second = widget._rows["second"]
    first.setFocus()
    QTest.keyClick(first, Qt.Key.Key_Down)
    assert second.hasFocus()
    QTest.keyClick(second, Qt.Key.Key_Return)
    assert switched == ["second"]
    QTest.keyClick(second, Qt.Key.Key_F2)
    assert renamed == [("second", "检查术语一致性")]
    QTest.keyClick(second, Qt.Key.Key_Delete)
    assert deleted == ["second"]

    QTest.keyClick(second, Qt.Key.Key_Tab)
    assert second._menu_btn.hasFocus()
    monkeypatch.setattr(QMenu, "exec", lambda menu, *_args: menu.actions()[0])
    QTest.keyClick(second._menu_btn, Qt.Key.Key_Space)
    assert len(renamed) == 2
    widget.close()


def test_full_name_is_used_for_rename_and_focus_survives_session_refresh(qapp, monkeypatch):
    widget = _sessions(qapp)
    full_name = "这是需要保留完整名称的会话" * 5
    values = [{"session_id": "first", "name": full_name}]
    widget.set_sessions(values)
    qapp.processEvents()
    row = widget._rows["first"]
    renamed = []
    widget.rename_session.connect(lambda _sid, name: renamed.append(name))
    monkeypatch.setattr(QMenu, "exec", lambda menu, *_args: menu.actions()[0])
    row._menu_btn.click()
    assert renamed == [full_name]
    assert row._name_label.full_text == full_name
    row.setFocus()
    widget.set_sessions(values)
    qapp.processEvents()
    assert widget._rows["first"].hasFocus()
    widget.close()
