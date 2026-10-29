from __future__ import annotations

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QPushButton
import pytest

from transbridge.application.projects.source_input_candidates import InputNeed
from transbridge.ui.source_input_batch_dialog import SourceInputBatchDialog

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def dialog():
    subject = SourceInputBatchDialog(
        (
            InputNeed("ready", "Dialogue.esp", 7, True),
            InputNeed("weapons", "Weapons.esp", 128, False, "D:/lost/Weapons.esp"),
            InputNeed("quests", "Quests.esp", 342, False),
        ),
        allow_partial=True,
        operation_label="写回插件",
    )
    subject.show()
    _APP.processEvents()
    yield subject
    subject.close()
    subject.deleteLater()
    _APP.processEvents()


def _cell(model, row, column):
    return model.data(model.index(row, column))


def test_missing_list_counts_and_known_inputs_are_collapsed(dialog):
    assert dialog.model.rowCount() == 2
    assert _cell(dialog.model, 0, 0) == "Weapons.esp"
    assert _cell(dialog.model, 0, 1) == 128
    assert "缺少写回模板" in _cell(dialog.model, 0, 3)
    assert dialog.known_model.rowCount() == 1
    assert not dialog.known_table.isVisible()
    assert "1 / 3" in dialog.progress.format()
    assert not dialog.review_button.isEnabled()
    dialog.known_toggle.click()
    assert dialog.known_table.isVisible()
    assert "无需原来的来源路径" in _cell(dialog.known_model, 0, 3)


def test_partial_scope_requires_explicit_skip_and_review_before_continuing(dialog):
    scopes = []
    dialog.continued.connect(scopes.append)
    dialog.review_button.click()
    assert dialog.pages.currentIndex() == 0
    dialog.skip_checkbox.click()
    assert dialog.review_button.isEnabled()
    assert _cell(dialog.model, 0, 2) == "本次跳过"
    dialog.review_button.click()
    assert scopes == []
    assert dialog.pages.currentIndex() == 1
    assert dialog.title.text() == "确认本次操作范围"
    assert "写回插件" in dialog.context.text()
    assert _cell(dialog.scope_model, 0, 2) == "将写回"
    assert "将写回 1 个输入、7 条译文" in dialog.scope_summary.text()
    assert "跳过 2 个输入、470 条译文" in dialog.scope_summary.text()
    assert _cell(dialog.scope_model, 1, 2) == "本次跳过"
    assert "译文仍保留" in _cell(dialog.scope_model, 1, 3)
    dialog.confirm_button.click()
    dialog.confirm_button.click()
    assert scopes == [("ready",)]
    assert dialog.isVisible()  # The coordinator closes the dialog only when its operation finishes.


def test_back_allows_scope_adjustments_and_repaired_rows_stay_visible(dialog):
    dialog.skip_checkbox.click()
    dialog.review_button.click()
    dialog.back_button.click()
    assert dialog.pages.currentIndex() == 0
    repaired = tuple(replace(need, ready=True) for need in dialog._needs)
    dialog.set_needs(repaired)
    assert dialog.model.rowCount() == 2
    assert _cell(dialog.model, 0, 2) == "已补齐"
    assert dialog.known_model.rowCount() == 1
    dialog.skip_checkbox.setChecked(False)
    assert dialog.review_button.isEnabled()
    dialog.review_button.click()
    assert "将写回 3 个输入、477 条译文" in dialog.scope_summary.text()


def test_multiple_candidates_require_explicit_choice_and_emit_full_path(dialog):
    paths = ("D:/Mods/Current/Quests.esp", "D:/Backup/Old/Quests.esp")
    requests = []
    dialog.candidate_requested.connect(lambda *args: requests.append(args))
    dialog.set_candidates({"quests": paths})
    dialog.table.setCurrentIndex(dialog.model.index(1, 0))
    assert dialog.candidate_selector.currentData() is None
    assert not dialog.candidate_button.isEnabled()
    assert _cell(dialog.model, 1, 2) == "需要确认"
    assert dialog.candidate_selector.itemText(2) == paths[1]
    assert dialog.candidate_selector.itemData(2, Qt.ItemDataRole.ToolTipRole) == paths[1]
    dialog.candidate_selector.setCurrentIndex(2)
    assert dialog.candidate_selector.toolTip() == paths[1]
    assert requests == []
    dialog.candidate_button.click()
    assert requests == [("quests", paths[1])]
    assert not dialog._needs[2].ready


def test_single_candidate_requires_supplement_button(dialog):
    requests = []
    path = "D:/Recovered/Weapons.esp"
    dialog.candidate_requested.connect(lambda *args: requests.append(args))
    dialog.set_candidates({"weapons": (path,)})
    assert requests == []
    assert dialog.candidate_selector.currentData() == path
    assert dialog.candidate_button.isEnabled()
    assert not dialog.review_button.isEnabled()
    dialog.candidate_button.click()
    assert requests == [("weapons", path)]


def test_picker_requests_are_separate_and_do_not_open_files(dialog):
    requests = []
    dialog.files_requested.connect(lambda: requests.append("files"))
    dialog.directory_requested.connect(lambda: requests.append("directory"))
    dialog.source_requested.connect(lambda key: requests.append(key))
    dialog.files_button.click()
    dialog.directory_button.click()
    dialog.source_button.click()
    assert requests == ["files", "directory", "weapons"]


def test_busy_prevents_changes_but_allows_cancellation(dialog):
    scopes = []
    rejected = []
    dialog.continued.connect(scopes.append)
    dialog.rejected.connect(lambda: rejected.append(True))
    dialog.skip_checkbox.click()
    dialog.review_button.click()
    dialog.set_busy(True)
    assert not dialog.files_button.isEnabled()
    assert not dialog.directory_button.isEnabled()
    assert not dialog.table.isEnabled()
    assert not dialog.back_button.isEnabled()
    assert not dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    assert scopes == []
    dialog.cancel_button.click()
    assert rejected == [True]


def test_error_feedback_keeps_dialog_open_for_retry(dialog):
    scopes = []
    dialog.continued.connect(scopes.append)
    dialog.skip_checkbox.click()
    dialog.review_button.click()
    dialog.confirm_button.click()
    dialog.set_feedback("无法准备该输入，请重新选择对应文件。", error=True)
    assert dialog.feedback.text() == "无法准备该输入，请重新选择对应文件。"
    assert dialog.feedback.property("tbSemanticState") == "error"
    assert dialog.confirm_button.isEnabled()
    assert dialog.isVisible()
    dialog.back_button.click()
    assert dialog.pages.currentIndex() == 0
    assert scopes == [("ready",)]


def test_partial_disabled_and_zero_ready_never_allow_empty_scope():
    need = InputNeed("missing", "Weapons.esp", 128, False)
    for partial in (False, True):
        subject = SourceInputBatchDialog((need,), allow_partial=partial, requirement="reference")
        subject.show()
        assert subject.skip_checkbox.isVisible() is partial
        subject.skip_checkbox.setChecked(True)
        assert not subject.review_button.isEnabled()
        assert "缺少原文参考" in _cell(subject.model, 0, 3)
        subject.close()
        subject.deleteLater()


def test_large_list_uses_models_with_constant_widget_count():
    needs = tuple(InputNeed(str(index), f"Plugin{index}.esp", index, False) for index in range(1500))
    subject = SourceInputBatchDialog(needs)
    assert subject.model.rowCount() == 1500
    assert len(subject.findChildren(QComboBox)) == 1
    assert len(subject.findChildren(QPushButton)) < 12
    subject.close()
    subject.deleteLater()


def test_escape_rejects_without_continuing(dialog):
    scopes = []
    rejected = []
    dialog.continued.connect(scopes.append)
    dialog.rejected.connect(lambda: rejected.append(True))
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert rejected == [True]
    assert scopes == []


def test_changed_needs_invalidates_visible_scope_confirmation(dialog):
    dialog.skip_checkbox.click()
    dialog.review_button.click()
    dialog.set_needs(tuple(replace(need, ready=False) for need in dialog._needs))
    assert dialog.pages.currentIndex() == 0
    assert not dialog.review_button.isEnabled()
    assert dialog.model.rowCount() == 3
    assert dialog.known_model.rowCount() == 0
