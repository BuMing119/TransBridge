"""Secondary AI windows keep destructive actions explicit and read-only data truthful."""

from types import SimpleNamespace

from PyQt6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QTimer
from PyQt6.QtGui import QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService
from transbridge.ui.tools.ai_translator import _report_history_dialog as history_module
from transbridge.ui.tools.ai_translator._term_editor_dialog import _TermEditorDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def history(qapp, layout_app, tmp_path, monkeypatch):
    monkeypatch.setattr(history_module, "get_data_dir", lambda: str(tmp_path))
    directory = tmp_path / "ai_translator" / "Fixture" / "reports"
    directory.mkdir(parents=True)
    report = directory / "Fixture_translate_report_20261019_001000.xlsx"
    report.write_bytes(b"task-owned report fixture")
    dialog = history_module._ReportHistoryDialog()
    dialog.show()
    settle_layout(layout_app)
    yield dialog, report
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    settle_layout(layout_app)


def test_deleting_last_report_clears_selection_and_disables_repeat_delete(history, layout_app, monkeypatch):
    dialog, report = history
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    dialog._table.selectRow(0)
    QTest.mouseClick(dialog._btn_delete, Qt.MouseButton.LeftButton)
    settle_layout(layout_app)
    assert not report.exists()
    assert dialog._empty_label.isVisible()
    assert dialog._table.rowCount() == 0
    assert not dialog._table.selectedItems()
    assert not dialog._btn_delete.isEnabled()


def test_report_delete_confirmation_return_keeps_file(history, layout_app):
    dialog, report = history
    observed = []

    def confirm_with_return():
        message = QApplication.activeModalWidget()
        observed.append(message.standardButton(message.defaultButton()))
        QTest.keyClick(message, Qt.Key.Key_Return)

    dialog._table.selectRow(0)
    QTimer.singleShot(0, confirm_with_return)
    QTest.mouseClick(dialog._btn_delete, Qt.MouseButton.LeftButton)
    settle_layout(layout_app)
    assert report.exists()
    assert observed == [QMessageBox.StandardButton.No]
    assert dialog._table.rowCount() == 1


def test_failed_report_delete_preserves_row_for_retry_and_reports_cause(history, layout_app, monkeypatch):
    dialog, report = history
    warnings = []
    original_remove = history_module.os.remove

    def denied(_path):
        raise PermissionError("report is open in another program")

    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    monkeypatch.setattr(history_module.os, "remove", denied)
    dialog._table.selectRow(0)
    QTest.mouseClick(dialog._btn_delete, Qt.MouseButton.LeftButton)
    assert report.exists() and dialog._table.rowCount() == 1
    assert "report is open in another program" in warnings[0]
    assert dialog._btn_delete.isEnabled()
    monkeypatch.setattr(history_module.os, "remove", original_remove)
    QTest.mouseClick(dialog._btn_delete, Qt.MouseButton.LeftButton)
    settle_layout(layout_app)
    assert not report.exists() and not dialog._btn_delete.isEnabled()


def test_bulk_delete_keeps_confirmed_paths_when_a_new_report_arrives(history, layout_app, monkeypatch):
    dialog, first = history
    second = first.with_name("Fixture_translate_report_20261019_002000.xlsx")
    second.write_bytes(b"second owned report")
    incoming = first.with_name("Fixture_translate_report_20261019_003000.xlsx")
    dialog._populate()
    dialog._table.selectAll()
    original_remove = history_module.os.remove
    removed = []

    def remove_and_publish(path):
        removed.append(path)
        original_remove(path)
        incoming.write_bytes(b"new unselected report")

    monkeypatch.setattr(history_module.os, "remove", remove_and_publish)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes)
    QTest.mouseClick(dialog._btn_delete, Qt.MouseButton.LeftButton)
    settle_layout(layout_app)
    assert set(removed) == {str(first), str(second)}
    assert incoming.exists() and not first.exists() and not second.exists()
    assert dialog._table.rowCount() == 1 and not dialog._btn_delete.isEnabled()


def test_report_table_return_closes_without_starting_delete(history, layout_app, monkeypatch):
    dialog, report = history
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: questions.append(True) or QMessageBox.StandardButton.No)
    dialog._table.selectRow(0)
    dialog._table.setFocus()
    QTest.keyClick(dialog._table, Qt.Key.Key_Return)
    settle_layout(layout_app)
    assert not questions and report.exists() and not dialog.isVisible()


def test_dynamic_terms_are_read_only_instead_of_accepting_lost_edits(qapp, layout_app):
    entry = SimpleNamespace(term="Source", translation="Saved translation", source="dynamic", context="Fixture")
    dialog = _TermEditorDialog(SimpleNamespace(as_list=lambda: [entry]))
    dialog.show()
    settle_layout(layout_app)
    try:
        cell = dialog._table.item(0, 1)
        dialog._table.setCurrentItem(cell)
        dialog._table.setFocus()
        QTest.keyClick(dialog._table, Qt.Key.Key_F2)
        QTest.keyClicks(dialog._table, "replacement")
        settle_layout(layout_app)
        assert not any(editor.isVisible() for editor in dialog._table.findChildren(QLineEdit))
        assert cell.text() == entry.translation
        assert "编辑" not in dialog.windowTitle()
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settle_layout(layout_app)


def test_secondary_dialogs_keep_actions_reachable_and_background_current_across_themes(history, layout_app):
    report, _path = history
    entry = SimpleNamespace(
        term="Source " * 20, translation="Translation " * 20, source="dynamic", context="Long " * 30
    )
    terms = _TermEditorDialog(SimpleNamespace(as_list=lambda: [entry]))
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=ThemeMode.LIGHT, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    try:
        for mode in (ThemeMode.DARK, ThemeMode.LIGHT):
            result = service.set_preference(ThemePreference(mode), persist=False)
            assert result.status is not ThemeApplyStatus.FAILED
            for dialog in (report, terms):
                dialog.resize(600, 400)
                dialog.show()
                settle_layout(layout_app)
                assert dialog.width() == 600
                assert dialog.grab().toImage().pixelColor(2, 2) == result.snapshot.palette.color(
                    QPalette.ColorRole.Window
                )
                viewport = dialog._table.viewport()
                surface = viewport.mapTo(dialog, QPoint(5, viewport.height() - 5))
                assert dialog.grab().toImage().pixelColor(surface) == result.snapshot.palette.color(
                    QPalette.ColorRole.Base
                )
                frame = dialog.grab().toImage()
                cell = dialog._table.visualItemRect(dialog._table.item(0, 0))
                cell.translate(viewport.mapTo(dialog, QPoint()))
                cell = cell.intersected(dialog.rect())
                expected_text = result.snapshot.palette.color(QPalette.ColorRole.Text)
                assert any(
                    frame.pixelColor(x, y) == expected_text
                    for x in range(cell.left(), cell.right() + 1)
                    for y in range(cell.top(), cell.bottom() + 1)
                ), "Unselected table text must use the active theme foreground"
                assert dialog._table.isVisible()
                if dialog is terms:
                    assert dialog._table.horizontalScrollBar().maximum() > 0
                for button in dialog.findChildren(QPushButton):
                    assert dialog.rect().contains(button.geometry())
    finally:
        terms.close()
        terms.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        settle_layout(layout_app)
        service.close()
