"""Exercise destructive cloud confirmations through their real Qt dialogs."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem
import pytest

from transbridge.ui.paratranz import files_tab, members_tab, overview_tab, strings_tab, terms_tab


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in self.callbacks:
            callback(*args)


class _ImmediateWorker:
    def __init__(self, fn):
        self.fn = fn
        self.result = _Signal()
        self.error = _Signal()

    def start(self):
        self.result.emit(self.fn())


_CASES = (
    (overview_tab, "OverviewTab", "_delete_project", "ParatranzProjectAPI", "delete_project", (7,), "删除项目"),
    (files_tab, "FilesTab", "_delete_file", "ParatranzFilesAPI", "delete_file", (7, 11), "删除文件"),
    (
        strings_tab,
        "StringsTab",
        "_batch_delete",
        "ParatranzStringsAPI",
        "batch_strings",
        (7, "delete", [11]),
        "删除词条",
    ),
    (terms_tab, "TermsTab", "_delete_term", "ParatranzTermsAPI", "delete_term", (7, 11), "删除术语"),
    (members_tab, "MembersTab", "_remove_member", "ParatranzMembersAPI", "delete_member", (7, 11), "移除成员"),
)


@pytest.mark.parametrize(
    ("module", "widget_name", "handler_name", "api_name", "method_name", "expected_args", "action_text"),
    _CASES,
    ids=("project", "file", "strings", "term", "member"),
)
@pytest.mark.parametrize("interaction", ("enter", "escape", "cancel", "delete", "close"))
def test_cloud_removal_requires_explicit_choice(
    qapp, monkeypatch, module, widget_name, handler_name, api_name, method_name, expected_args, action_text, interaction
):
    context = SimpleNamespace(
        config=SimpleNamespace(token="test-only"),
        current_project={"id": 7, "name": "Fixture project"},
        project_selected=_Signal(),
        project_list_changed=_Signal(),
        paratranz_permissions_changed=_Signal(),
        is_admin=lambda: True,
    )
    # This fake remote state changes only when the original deletion request runs.
    remote = {"selected": {"id": expected_args[-1]}}

    def delete_remote(*args):
        assert args == expected_args
        del remote["selected"]

    api = SimpleNamespace(**{method_name: delete_remote})
    monkeypatch.setattr(module, api_name, lambda **_kwargs: api)
    monkeypatch.setattr(module, "ApiWorker", _ImmediateWorker)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    widget = getattr(module, widget_name)(context)
    widget._project_id = 7
    for name in ("load_files", "load_strings", "load_terms", "load_members"):
        if hasattr(widget, name):
            monkeypatch.setattr(widget, name, Mock())
    if hasattr(widget, "_table"):
        table = widget._table
        table.setRowCount(1)
        item = QTableWidgetItem("Fixture selection")
        item.setData(
            Qt.ItemDataRole.UserRole,
            {"id": 11, "name": "fixture.zip", "term": "Fixture term", "user": {"nickname": "Fixture member"}},
        )
        table.setItem(0, 0, item)
        table.selectRow(0)
    widget.show()
    qapp.processEvents()
    observed = []
    errors = []

    def answer():
        dialog = qapp.activeModalWidget()
        try:
            assert isinstance(dialog, QMessageBox)
            qapp.processEvents()
            cancel = dialog.button(QMessageBox.StandardButton.No)
            delete = dialog.button(QMessageBox.StandardButton.Yes)
            assert cancel.text() == "取消"
            assert delete.text() == action_text
            assert dialog.defaultButton() is cancel
            assert dialog.escapeButton() is cancel
            assert cancel.hasFocus()
            assert not delete.isDefault()
            observed.append(True)
            if interaction == "enter":
                QTest.keyClick(dialog, Qt.Key.Key_Return)
            elif interaction == "escape":
                delete.setFocus()
                QTest.keyClick(delete, Qt.Key.Key_Escape)
            elif interaction == "close":
                dialog.close()
            else:
                QTest.mouseClick(delete if interaction == "delete" else cancel, Qt.MouseButton.LeftButton)
        except BaseException as exc:
            errors.append(exc)
            if isinstance(dialog, QMessageBox):
                dialog.reject()

    QTimer.singleShot(0, answer)
    try:
        getattr(widget, handler_name)()
        if errors:
            raise errors[0]
        assert observed == [True]
        assert ("selected" not in remote) is (interaction == "delete")
        if module is overview_tab:
            assert (context.current_project is None) is (interaction == "delete")
    finally:
        widget.close()
        widget.deleteLater()
        qapp.processEvents()
