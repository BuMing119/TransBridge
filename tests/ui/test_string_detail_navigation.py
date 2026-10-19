"""Real selection and paging must preserve the string editor's visible target."""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.config.ui_preferences import DEFAULT_THEME_ID, ThemeMode
from transbridge.ui.foundation.builtins import create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemeApplyStatus, ThemePreference, ThemeService
from transbridge.ui.paratranz import string_detail_dialog as module
from transbridge.ui.workers import ApiWorker


class _Worker(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()
    instances = []

    def __init__(self, call, **_kwargs):
        super().__init__()
        self.call = call
        self.instances.append(self)

    def start(self):
        pass

    def isRunning(self):  # noqa: N802
        return False


@pytest.fixture
def dialog(layout_app, monkeypatch):
    _Worker.instances.clear()
    monkeypatch.setattr(module, "ApiWorker", _Worker)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: QMessageBox.StandardButton.Ok)
    context = SimpleNamespace(config=SimpleNamespace(token="synthetic"), current_user={"id": 1})
    rows = [
        {"id": index, "key": f"key-{index}", "original": f"Text {index}", "translation": f"Saved {index}", "stage": 0}
        for index in (1, 2)
    ]
    view = module.StringDetailDialog(rows, 0, context, 1, page=2, page_size=2)
    view.show()
    settle_layout(layout_app)
    yield view
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.StandardButton.Discard)
    view.close()


def test_keyboard_selection_updates_editor_and_cancel_keeps_dirty_input(dialog, monkeypatch):
    dialog._nav_list.setFocus()
    QTest.keyClick(dialog._nav_list, Qt.Key.Key_Down)
    assert dialog._det_key.text() == "key-2"
    dialog._trans_edit.setPlainText("unsaved local text")
    QTest.keyClick(dialog._nav_list, Qt.Key.Key_Up)
    assert dialog._nav_list.currentRow() == 1
    assert dialog._current_idx == 1
    assert dialog._trans_edit.toPlainText() == "unsaved local text"
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: QMessageBox.StandardButton.Discard)
    QTest.keyClick(dialog._nav_list, Qt.Key.Key_Up)
    assert dialog._det_key.text() == "key-1"


def test_empty_filter_disables_editor_and_cannot_save_previous_string(dialog):
    dialog._modifier_group.button(1).click()
    assert dialog._nav_list.count() == 0
    assert not dialog._save_btn.isEnabled()
    assert not dialog._trans_edit.isEnabled()
    dialog._save()
    assert not _Worker.instances
    dialog._modifier_group.button(0).click()
    assert dialog._save_btn.isEnabled()
    assert dialog._trans_edit.isEnabled()
    assert dialog._trans_edit.toPlainText() == "Saved 1"


def test_filter_that_keeps_current_row_preserves_unsaved_text(dialog):
    dialog._trans_edit.setPlainText("unsaved local text")
    dialog._apply_nav_filter()
    assert dialog._trans_edit.toPlainText() == "unsaved local text"


def test_failed_page_restores_retry_buttons_and_retains_current_record(dialog):
    dialog._next_page_btn.click()
    assert not dialog._next_page_btn.isEnabled()
    _Worker.instances[-1].error.emit("synthetic page failure")
    assert dialog._prev_page_btn.isEnabled()
    assert dialog._next_page_btn.isEnabled()
    assert dialog._det_key.text() == "key-1"
    dialog._next_page_btn.click()
    assert len(_Worker.instances) == 2
    _Worker.instances[0].error.emit("late failure from the previous request")
    assert dialog._page_lbl.text() == "加载中…"
    assert not dialog._next_page_btn.isEnabled()
    _Worker.instances[-1].result.emit([])
    assert dialog._page == 2
    assert dialog._det_key.text() == "key-1"
    assert dialog._save_btn.isEnabled()
    assert not dialog._next_page_btn.isEnabled()


def test_new_filter_with_empty_first_page_clears_old_record(dialog):
    dialog._stage_checks[5].click()
    _Worker.instances[-1].result.emit([])
    assert dialog._nav_list.count() == 0
    assert not dialog._save_btn.isEnabled()
    assert dialog._current_idx == -1


def test_cancelled_filter_navigation_and_close_preserve_dirty_record(dialog):
    dialog._trans_edit.setPlainText("keep this edit")
    dialog._stage_edit.setCurrentIndex(dialog._stage_edit.findData(1))
    dialog._modifier_group.button(1).click()
    assert dialog._modifier_group.checkedId() == 0
    assert dialog._nav_list.count() == 2
    dialog._stage_checks[5].click()
    assert not dialog._stage_checks[5].isChecked()
    dialog._next_page_btn.click()
    dialog.reject()
    assert dialog.isVisible()
    assert not _Worker.instances
    assert dialog._det_key.text() == "key-1"
    assert dialog._trans_edit.toPlainText() == "keep this edit"
    assert dialog._stage_edit.currentData() == 1


def test_failed_filter_load_restores_accepted_filter_and_editor(dialog):
    dialog._stage_checks[5].click()
    assert not dialog._trans_edit.isEnabled()
    assert not dialog._nav_list.isEnabled()
    _Worker.instances[-1].error.emit("synthetic permission error")
    assert not dialog._stage_checks[5].isChecked()
    assert dialog._current_api_stage is None
    assert dialog._save_btn.isEnabled()
    assert dialog._det_key.text() == "key-1"
    assert "synthetic permission error" in dialog._page_lbl.toolTip()


def test_save_failure_preserves_input_and_retry_updates_only_visible_record(dialog):
    dialog._trans_edit.setPlainText("retry this translation")
    dialog._save_btn.click()
    assert not dialog._nav_list.isEnabled()
    assert not dialog._trans_edit.isEnabled()
    assert not dialog._stage_edit.isEnabled()
    dialog._save()
    assert len(_Worker.instances) == 1
    _Worker.instances[-1].error.emit("synthetic save failure")
    assert dialog._save_btn.isEnabled()
    assert dialog._trans_edit.toPlainText() == "retry this translation"
    assert dialog._strings[0]["translation"] == "Saved 1"
    dialog._save_btn.click()
    _Worker.instances[-1].result.emit({})
    assert dialog._strings[0]["translation"] == "retry this translation"
    assert dialog._strings[1]["translation"] == "Saved 2"
    assert dialog.was_modified()


def test_saved_record_filtered_out_does_not_reenable_empty_editor(dialog):
    dialog._strings = dialog._strings[:1]
    checkbox = dialog._stage_checks[0]
    checkbox.blockSignals(True)
    checkbox.setChecked(True)
    checkbox.blockSignals(False)
    dialog._apply_nav_filter()
    dialog._stage_edit.setCurrentIndex(dialog._stage_edit.findData(5))
    dialog._save_btn.click()
    _Worker.instances[-1].result.emit({})
    assert dialog._nav_list.count() == 0
    assert dialog._current_idx == -1
    assert not dialog._save_btn.isEnabled()
    assert not dialog._stage_edit.isEnabled()


def test_real_http_error_restores_paging_without_external_service(dialog, monkeypatch):
    class API:
        def __init__(self, **_kwargs):
            pass

        def list_strings(self, *_args, **_kwargs):
            raise RuntimeError("HTTP 403: synthetic permission error")

    monkeypatch.setattr(module, "ApiWorker", ApiWorker)
    monkeypatch.setattr(module, "ParatranzStringsAPI", API)
    dialog._next_page_btn.click()
    for _ in range(100):
        if dialog._next_page_btn.isEnabled():
            break
        QTest.qWait(10)
    assert dialog._next_page_btn.isEnabled()
    assert dialog._trans_edit.isEnabled()
    assert "403" in dialog._page_lbl.toolTip()
    assert dialog._workers[-1].wait(1000)


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_narrow_dialog_keeps_navigation_and_editor_actions_visible(dialog, layout_app, mode):
    preferences = SimpleNamespace(
        load=lambda: SimpleNamespace(theme_mode=mode, theme_id=DEFAULT_THEME_ID, diagnostics=())
    )
    service = ThemeService(layout_app, create_builtin_registry(), preferences)
    service.start()
    try:
        result = service.set_preference(ThemePreference(mode, DEFAULT_THEME_ID), persist=False)
        assert result.status is not ThemeApplyStatus.FAILED
        assert result.snapshot.effective_scheme.value == mode.value
        dialog.resize(760, 480)
        settle_layout(layout_app)
        for widget in (dialog._nav_list, dialog._prev_btn, dialog._next_btn, dialog._save_btn):
            assert widget.isVisible()
            assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().topLeft()))
            assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
        dialog._nav_list.setFocus()
        QTest.keyClick(dialog._nav_list, Qt.Key.Key_Down)
        assert dialog._det_key.text() == "key-2"
    finally:
        service.close()
