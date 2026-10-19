from __future__ import annotations

from types import SimpleNamespace

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton
import pytest

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.tools.ai_translator._polish_preview_dialog import _PolishPreviewDialog


def _entry(entry_id: str) -> TranslationEntry:
    return TranslationEntry(
        id=entry_id,
        key=f"key-{entry_id}",
        original=f"Original {entry_id}",
        translation=f"当前 {entry_id}",
        stage=1,
        context="DIAL:FULL",
    )


def _result(entry_id: str, *, confidence: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(
        confidence=confidence,
        issues=(SimpleNamespace(severity="warning", message="措辞问题"),),
        refined_translation=f"纠错 {entry_id}",
        polished_translation=f"最终 {entry_id}",
        verdict="pass",
        note="裁决通过",
    )


@pytest.fixture
def dialog_factory():
    app = QApplication.instance() or QApplication([])
    dialogs = []

    def create(entries, results):
        dialog = _PolishPreviewDialog(entries, results)
        dialogs.append(dialog)
        dialog.show()
        app.processEvents()
        return dialog

    yield create
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def test_preview_populates_all_columns_and_connects_click_once() -> None:
    app = QApplication.instance() or QApplication([])
    entries = [_entry("one"), _entry("two")]
    results = {entry.id: _result(entry.id) for entry in entries}
    dialog = _PolishPreviewDialog(entries, results)

    assert [dialog._table.item(0, col).text() for col in range(6)] == [
        "Original one",
        "当前 one",
        "[warning] 措辞问题",
        "纠错 one",
        "最终 one",
        "pass (0.90)\n裁决通过",
    ]

    dialog._table.cellClicked.emit(0, 4)

    assert dialog.get_results() == {"one": "最终 one", "two": None}
    dialog.close()
    app.processEvents()


def test_candidate_clicks_and_keyboard_choices_are_reversible(dialog_factory) -> None:
    dialog = dialog_factory([_entry("one")], {"one": _result("one")})
    table = dialog._table
    candidate = table.item(0, 4)
    choice = table.item(0, 6)
    assert choice.text() == "待选择"
    assert choice.checkState() == Qt.CheckState.PartiallyChecked

    for expected in ("最终 one", None, "最终 one"):
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(candidate).center())
        assert dialog.get_results() == {"one": expected}
        assert choice.text() == ("采用候选" if expected else "保留原译文")

    table.setCurrentItem(choice)
    table.setFocus()
    for expected in (None, "最终 one", None):
        QTest.keyClick(table, Qt.Key.Key_Space)
        assert dialog.get_results() == {"one": expected}
        assert choice.checkState() == (Qt.CheckState.Checked if expected else Qt.CheckState.Unchecked)


def test_explicit_checkbox_can_adopt_a_pending_candidate_with_mouse(dialog_factory) -> None:
    dialog = dialog_factory([_entry("one")], {"one": _result("one")})
    table = dialog._table
    choice = table.item(0, 6)
    rect = table.visualItemRect(choice)
    point = rect.center()
    point.setX(rect.left() + 10)

    for expected in ("最终 one", None, "最终 one"):
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert dialog.get_results() == {"one": expected}


def test_bulk_choices_restore_rejected_candidates_but_never_accept_failures(dialog_factory) -> None:
    entries = [_entry(name) for name in ("one", "failed", "missing")]
    dialog = dialog_factory(entries, {"one": _result("one"), "failed": _result("failed", confidence=0)})
    expected_adopted = {"one": "最终 one", "failed": None, "missing": None}
    expected_preserved = dict.fromkeys(expected_adopted)

    for button, expected in (
        (dialog._accept_all_btn, expected_adopted),
        (dialog._reject_all_btn, expected_preserved),
        (dialog._accept_all_btn, expected_adopted),
        (dialog._reject_all_btn, expected_preserved),
    ):
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert dialog.get_results() == expected
        assert "失败 2" in dialog._stats_lbl.text()

    table = dialog._table
    for row in (1, 2):
        choice = table.item(row, 6)
        assert choice.text() == "生成失败"
        assert not choice.flags() & Qt.ItemFlag.ItemIsEnabled
        assert not choice.flags() & Qt.ItemFlag.ItemIsUserCheckable
        QTest.mouseClick(
            table.viewport(), Qt.MouseButton.LeftButton, pos=table.visualItemRect(table.item(row, 4)).center()
        )
        table.setCurrentItem(choice)
        QTest.keyClick(table, Qt.Key.Key_Space)
        assert dialog.get_results() == expected_preserved


def test_unreviewed_choices_still_require_confirmation_and_keep_original(dialog_factory, monkeypatch) -> None:
    dialog = dialog_factory([_entry("one")], {"one": _result("one")})
    prompts = []

    def decline(*args):
        prompts.append(args[2])
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    QTest.mouseClick(dialog._apply_btn, Qt.MouseButton.LeftButton)
    assert dialog.isVisible()
    assert len(prompts) == 1 and "1 条未处理" in prompts[0]
    assert dialog.get_results() == {"one": None}

    QTest.mouseClick(dialog._reject_all_btn, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog._apply_btn, Qt.MouseButton.LeftButton)
    assert dialog.result() == dialog.DialogCode.Accepted
    assert len(prompts) == 1
    assert dialog.get_results() == {"one": None}


def test_enter_in_preview_table_does_not_change_bulk_choices_or_apply(dialog_factory):
    entries = [_entry("one"), _entry("two")]
    dialog = dialog_factory(entries, {entry.id: _result(entry.id) for entry in entries})
    dialog._table.setCurrentCell(0, 0)
    dialog._table.setFocus()
    before = dict(dialog._row_status)

    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        QTest.keyClick(dialog._table, key)

    assert dialog.isVisible()
    assert dialog._row_status == before
    assert dialog.get_results() == {"one": None, "two": None}

    dialog._reject_all_btn.setFocus()
    QTest.keyClick(dialog._reject_all_btn, Qt.Key.Key_Return)
    dialog._table.setCurrentCell(0, 6)
    dialog._table.setFocus()
    QTest.keyClick(dialog._table, Qt.Key.Key_Space)
    QTest.keyClick(dialog._table, Qt.Key.Key_Return)
    assert dialog.get_results() == {"one": "最终 one", "two": None}
    dialog._apply_btn.setFocus()
    QTest.keyClick(dialog._apply_btn, Qt.Key.Key_Return)
    assert dialog.result() == dialog.DialogCode.Accepted


def test_escape_cancels_preview_and_declines_unreviewed_apply(dialog_factory):
    dialog = dialog_factory([_entry("one")], {"one": _result("one")})
    prompts = []

    def decline_prompt():
        prompt = QApplication.activeModalWidget()
        prompts.append(prompt)
        QTest.keyClick(prompt, Qt.Key.Key_Escape)

    dialog._apply_btn.setFocus()
    QTimer.singleShot(0, decline_prompt)
    QTest.keyClick(dialog._apply_btn, Qt.Key.Key_Return)
    assert len(prompts) == 1
    assert dialog.isVisible()
    assert dialog.get_results() == {"one": None}
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.result() == dialog.DialogCode.Rejected
    assert not dialog.isVisible()


def test_preview_table_arrows_review_cells_and_tab_reaches_actions(dialog_factory):
    dialog = dialog_factory([_entry("one")], {"one": _result("one")})
    table = dialog._table
    cancel = next(button for button in dialog.findChildren(QPushButton) if button.text() == "取消")
    table.setCurrentCell(0, 0)
    table.setFocus()
    QTest.keyClick(table, Qt.Key.Key_Right)
    assert table.currentColumn() == 1
    QTest.keyClick(table, Qt.Key.Key_Tab)
    assert cancel.hasFocus()
    QTest.keyClick(cancel, Qt.Key.Key_Tab)
    assert dialog._apply_btn.hasFocus()
    QTest.keyClick(dialog._apply_btn, Qt.Key.Key_Backtab)
    assert cancel.hasFocus()
    QTest.keyClick(cancel, Qt.Key.Key_Backtab)
    assert table.hasFocus()
    assert table.currentColumn() == 1
    assert dialog.get_results() == {"one": None}
