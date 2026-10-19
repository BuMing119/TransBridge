"""Common FOMOD inputs stay reachable while advanced rules remain optional."""

from pathlib import Path
import sys

from PyQt6.QtCore import QPoint, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog
import pytest

from tests.ui.responsive_layout_support import settle_layout, themed_layout_app as themed_layout_app
from transbridge.ui.tools.fomod.fomod_panel import FomodPanel


class _PlanDialog(QDialog):
    execution_status_changed = pyqtSignal(str, str)
    _execution_running = False


class _Facade:
    def __init__(self):
        self.calls = []

    def begin_fomod(self, context, *, draft, rules, parent):
        self.calls.append((draft, rules))
        dialog = _PlanDialog(parent)
        dialog.show()
        return dialog


@pytest.fixture
def panel(layout_app):
    view = FomodPanel(operation_plan_facade=_Facade())
    view.show()
    settle_layout(layout_app)
    yield view
    view.close()
    view.deleteLater()
    settle_layout(layout_app)


def test_first_step_exposes_common_inputs_and_explains_disabled_action(panel):
    assert panel._new_edit.isVisible() and panel._old_edit.isVisible() and panel._out_edit.isVisible()
    assert panel._advanced_body.isHidden()
    assert panel._result_group.isHidden()
    assert not panel._run_btn.isEnabled()
    assert "新版" in panel._ready_hint.text()
    assert "检查" in panel._run_btn.text()


def test_typing_source_suggests_output_and_preserves_manual_destination(panel):
    panel._new_edit.setText("D:/fixtures/new.zip")
    assert Path(panel._out_edit.text()).name == "new-translated.zip"
    assert panel._run_btn.isEnabled()
    panel._new_edit.clear()
    assert not panel._out_edit.text() and not panel._run_btn.isEnabled()
    panel._new_edit.setText("D:/fixtures/new.zip")
    panel._out_edit.setText("D:/chosen/manual.zip")
    panel._fmt_combo.setCurrentIndex(1)
    panel._new_edit.setText("D:/fixtures/another.7z")
    assert panel._out_edit.text() == "D:/chosen/manual.zip"
    panel._new_edit.clear()
    assert not panel._run_btn.isEnabled()


def test_keyboard_path_entry_handles_incomplete_drive_and_preserves_manual_output(panel, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    QTest.keyClicks(panel._new_edit, "D:/fixtures/new.zip")
    assert not errors
    assert Path(panel._out_edit.text()).name == "new-translated.zip"
    for source in ("D:", "D:/", ".", ".."):
        panel._new_edit.setText(source)
        assert not errors
        assert not panel._out_edit.text()
        assert not panel._run_btn.isEnabled()
    panel._out_edit.setText("D:/chosen/manual.zip")
    panel._new_edit.setText("D:")
    assert not errors
    assert panel._out_edit.text() == "D:/chosen/manual.zip"


def test_collapsing_advanced_rules_keeps_them_in_the_reviewed_plan(panel):
    panel._advanced_toggle.click()
    panel._keep_ext_edit.setText("dds, wav")
    panel._strip_ext_edit.setText("wav")
    before = panel._collect_rules()
    panel._advanced_toggle.click()
    assert panel._advanced_body.isHidden()
    assert panel._collect_rules() == before
    assert ".dds" in before.keep_exts and ".wav" in before.strip_exts
    assert "额外保留 2" in panel._filter_summary.text()
    panel.prefill_new_archive("D:/fixtures/new.zip")
    panel._run_btn.click()
    draft, rules = panel._operation_plan_facade.calls[0]
    assert draft.new_archive == "D:/fixtures/new.zip"
    assert rules == before
    assert panel._worker is None
    assert not panel._run_btn.isEnabled()
    assert not panel._form_body.isEnabled()
    assert panel._result_group.isVisible()
    assert "尚未开始" in panel._result_text.toPlainText()
    panel._plan_dialog.reject()
    assert panel._run_btn.isEnabled()
    assert panel._form_body.isEnabled()
    assert panel._result_text.property("tbStatusId") == "not-run"


@pytest.mark.parametrize("font_size", [9, 14])
@pytest.mark.parametrize("size", [(600, 560), (720, 580)])
def test_advanced_rules_and_results_do_not_push_actions_outside_window(panel, layout_app, font_size, size):
    panel.setFont(QFont("Microsoft YaHei", font_size))
    panel.resize(*size)
    for expanded in (False, True, False):
        panel._advanced_toggle.setChecked(expanded)
        panel._on_failed("归档无法读取，请重新选择输入文件。")
        settle_layout(layout_app)
        assert (panel.width(), panel.height()) == size
        assert panel._form_scroll.horizontalScrollBar().maximum() == 0
        for button in (panel._run_btn, panel._close_btn):
            rectangle = button.rect().translated(button.mapTo(panel, QPoint()))
            assert panel.rect().contains(rectangle)
        panel._form_scroll.ensureWidgetVisible(panel._out_edit)
        settle_layout(layout_app)
        rectangle = panel._out_edit.rect().translated(panel._out_edit.mapTo(panel._form_scroll.viewport(), QPoint()))
        assert panel._form_scroll.viewport().rect().contains(rectangle)


def test_editing_after_failure_marks_existing_result_as_previous_attempt(panel):
    panel.prefill_new_archive("D:/fixtures/new.zip")
    panel._on_failed("归档损坏")
    assert panel._result_group.isVisible()
    assert "归档损坏" in panel._result_text.toPlainText()
    panel._new_edit.setText("D:/fixtures/replacement.zip")
    assert panel._result_group.title() == "上次执行结果"
    assert "归档损坏" in panel._result_text.toPlainText()
    assert panel._run_btn.isEnabled()
