"""History browsing and result actions keep their place, scope, and visible feedback."""

from copy import deepcopy
import threading
from types import SimpleNamespace

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog, QToolButton
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from tests.ui.tools import test_ai_task_history_ui as fixtures
from tests.ui.tools.test_ai_task_history_ui import _record, _wait
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.model import ThemeScheme
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService
from transbridge.ui.tools.ai_translator import task_result_dialog
from transbridge.ui.tools.ai_translator.task_history_dialog import TaskHistoryDialog
from transbridge.ui.tools.ai_translator.task_result_dialog import TaskResultDialog

qapp = fixtures.qapp


@pytest.fixture
def views(qapp, layout_app):
    opened = []

    def create(record=None, *, history=False):
        if history:
            registry = SimpleNamespace(
                directory_provider=lambda: None,
                windows={},
                shutting_down=False,
                ctx=SimpleNamespace(),
                is_recovery_active=lambda _: False,
            )
            dialog = TaskHistoryDialog(registry)
            dialog.timer.stop()
        else:
            dialog = TaskResultDialog(_record() if record is None else record)
        opened.append(dialog)
        dialog.show()
        settle_layout(layout_app)
        if history:
            dialog.timer.stop()
        return dialog

    yield create
    for dialog in opened:
        _wait(layout_app, lambda: not dialog.busy)
        for child in getattr(dialog, "_dialogs", ()):
            child.close()
        dialog.close()
        dialog.deleteLater()
    settle_layout(layout_app)


def _history_records(count=80):
    return tuple(
        {
            "run_id": f"task-{index}",
            "created_at": f"2026-10-18T{index // 60:02}:{index % 60:02}:00",
            "sources": [{"label": f"来源 {index}"}],
        }
        for index in range(count)
    )


def _selected_key(dialog):
    item = dialog.tasks.currentItem()
    return item.data(0, Qt.ItemDataRole.UserRole) if item else None


def test_history_refresh_keeps_scrolled_rows_and_selection_when_records_change(views, layout_app):
    dialog = views(history=True)
    dialog._records = _history_records()
    dialog._populate()
    settle_layout(layout_app)
    dialog.tasks.setCurrentItem(dialog.tasks.topLevelItem(3))
    selected = _selected_key(dialog)
    scrollbar = dialog.tasks.verticalScrollBar()
    scrollbar.setValue(scrollbar.maximum() - 5)
    anchor = dialog.tasks.itemAt(QPoint(2, 2))
    anchor_key = anchor.data(0, Qt.ItemDataRole.UserRole)
    before = scrollbar.value()
    dialog._populate()
    settle_layout(layout_app)
    assert scrollbar.value() == before
    assert _selected_key(dialog) == selected
    updated = deepcopy(dialog._records)
    updated[20]["counts"] = {"successful": 7}
    dialog._records = (*updated, {**updated[0], "run_id": "new", "created_at": "2026-10-19T00:00:00"})
    dialog._populate()
    settle_layout(layout_app)
    assert dialog.tasks.itemAt(QPoint(2, 2)).data(0, Qt.ItemDataRole.UserRole) == anchor_key
    assert _selected_key(dialog) == selected
    row = next(
        dialog.tasks.topLevelItem(index)
        for index in range(dialog.tasks.topLevelItemCount())
        if dialog.tasks.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole) == "task-20"
    )
    assert "成功 7" in row.text(3)


def test_history_empty_and_busy_actions_are_disabled_and_keyboard_opens_selected(views, layout_app):
    dialog = views(history=True)
    assert not dialog.open_button.isEnabled()
    dialog._records = (_record(),)
    dialog.store = SimpleNamespace(load=lambda _: dialog._records[0])
    dialog._populate()
    settle_layout(layout_app)
    assert dialog.open_button.isEnabled()
    assert dialog.open_button.property("tbSemanticState") == "primary"
    dialog.worker = object()
    dialog._update_recovery_buttons()
    assert not dialog.open_button.isEnabled() and not dialog.refresh_button.isEnabled()
    dialog.worker = None
    dialog._update_recovery_buttons()
    dialog.tasks.setFocus()
    QTest.keyClick(dialog.tasks, Qt.Key.Key_Return)
    _wait(layout_app, lambda: not dialog.busy and len(dialog._dialogs) == 1)
    assert dialog._dialogs[0].record["run_id"] == "past-run"


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_long_result_metadata_and_source_fit_narrow_window_across_themes(views, layout_app, mode):
    record = _record()
    record["version_label"] = "<版本名称>" + "VeryLongVersion-" * 15
    record["sources"][0]["label"] = "SourceWithLongName-" * 15
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    assert service.start().status is not ThemeApplyStatus.FAILED
    dialog = views(record)
    dialog.setFont(QFont("Microsoft YaHei", 14))
    try:
        for current in (mode, ThemeMode.DARK if mode is ThemeMode.LIGHT else ThemeMode.LIGHT):
            result = service.set_preference(ThemePreference(current, DEFAULT_THEME_ID), persist=False)
            assert result.status is not ThemeApplyStatus.FAILED
            assert result.snapshot.effective_scheme is (
                ThemeScheme.LIGHT if current is ThemeMode.LIGHT else ThemeScheme.DARK
            )
            dialog.resize(720, 600)
            settle_layout(layout_app)
            assert dialog.width() == 720
            assert record["version_label"] in dialog.context_label.toolTip()
            assert dialog.context_label.textFormat() is Qt.TextFormat.PlainText
            assert dialog.sources.toolTip() == record["sources"][0]["label"]
            for widget in (dialog.sources, dialog.search, dialog.filter, *dialog.export_buttons, dialog.close_button):
                rect = widget.rect().translated(widget.mapTo(dialog, QPoint()))
                assert dialog.rect().contains(rect)
            assert not dialog.sources.geometry().intersects(dialog.search.geometry())
            assert not dialog.search.geometry().intersects(dialog.filter.geometry())
            assert dialog.export_buttons[0].property("tbSemanticState") == "primary"
            assert (
                dialog.grab().toImage().pixelColor(2, 2).name()
                == result.snapshot.palette.color(QPalette.ColorRole.Window).name()
            )
    finally:
        service.close()


def test_result_empty_and_filtered_states_explain_next_action_and_clear_search(views, layout_app):
    empty = views({"sources": []})
    assert all(not button.isEnabled() for button in empty.export_buttons)
    assert not empty.cancel_button.isVisible()
    assert "暂无" in empty.detail.placeholderText()
    empty.resume_exports()
    assert all(not button.isEnabled() for button in empty.export_buttons)

    record = _record()
    record["sources"][0]["snapshot"]["diagnostics"] = []
    result = views(record)
    assert "选择" in result.detail.placeholderText()
    assert not result.diagnostics.isVisible()
    result.search.setFocus()
    QTest.keyClicks(result.search, "no-match")
    assert result.model.rowCount() == 0
    assert "匹配" in result.detail.placeholderText()
    assert result.search.isClearButtonEnabled()
    clear = next(button for button in result.search.findChildren(QToolButton) if button.isVisible())
    QTest.mouseClick(clear, Qt.MouseButton.LeftButton)
    assert result.model.rowCount() == 2 and not result.search.text()
    result.table.setFocus()
    QTest.keyClick(result.table, Qt.Key.Key_Down)
    assert "Original first" in result.detail.toPlainText()
    result.sources.setCurrentIndex(1)
    assert result.model.rowCount() == 1 and result.diagnostics.isVisible()


def test_filtered_export_keeps_complete_source_scope_and_uses_semantic_feedback(
    views, layout_app, tmp_path, monkeypatch
):
    dialog = views()
    dialog.search.setText("first 1")
    assert dialog.model.rowCount() == 1
    original = task_result_dialog.export_snapshot
    counts = []

    def export(snapshot, *args, **kwargs):
        counts.append(len(snapshot.candidates))
        return original(snapshot, *args, **kwargs)

    monkeypatch.setattr(task_result_dialog, "export_snapshot", export)
    target = tmp_path / "all-source.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), "csv"))
    QTest.mouseClick(dialog.export_buttons[1], Qt.MouseButton.LeftButton)
    assert dialog.busy and dialog.cancel_button.isVisible()
    assert dialog.export_status.property("tbSemanticState") == "info"
    _wait(layout_app, lambda: not dialog.busy)
    assert target.exists() and counts == [2]
    assert dialog.export_status.property("tbSemanticState") == "success"
    assert not dialog.cancel_button.isVisible()


def test_export_failure_retry_cancel_and_close_preserve_background_contract(views, layout_app, tmp_path, monkeypatch):
    dialog = views()
    entered, release = threading.Event(), threading.Event()
    attempts = []

    def export(_snapshot, _path, _format, *, cancel_event):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("test output unavailable")
        entered.set()
        assert release.wait(3)
        assert cancel_event.is_set()
        raise RuntimeError("cancelled by test")

    monkeypatch.setattr(task_result_dialog, "export_snapshot", export)
    target = tmp_path / "result.csv"
    try:
        dialog.export(target, "csv")
        _wait(layout_app, lambda: not dialog.busy)
        assert dialog.export_status.property("tbSemanticState") == "error"
        assert all(button.isEnabled() for button in dialog.export_buttons)
        dialog.export(target, "csv")
        _wait(layout_app, entered.is_set)
        dialog.cancel_button.setFocus()
        QTest.keyClick(dialog.cancel_button, Qt.Key.Key_Space)
        assert "正在取消" in dialog.export_status.text() and not dialog.cancel_button.isEnabled()
        assert dialog.close() and dialog.busy
        release.set()
        _wait(layout_app, lambda: not dialog.busy)
        dialog.show()
        settle_layout(layout_app)
        assert "已取消" in dialog.export_status.text()
        assert dialog.export_status.property("tbSemanticState") == "info"
        assert not dialog.cancel_button.isVisible()
        assert not target.exists()
    finally:
        release.set()
