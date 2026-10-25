from __future__ import annotations

from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox
import pytest

from transbridge.ui.operations.plan_view import OperationKind
from transbridge.ui.operations.sync_review_dialog import SyncReviewDialog
from transbridge.ui.operations.sync_review_view import KEEP_LOCAL, KEEP_REMOTE, SyncReviewCandidate, SyncReviewRow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _rows() -> tuple[SyncReviewRow, ...]:
    return (
        SyncReviewRow(
            "local-a",
            "key-a",
            "<b>Original A</b>",
            "<img>本地 A",
            "local source.esp",
            "多个云端候选",
            (
                SyncReviewCandidate("a1", "file.json · ID 71", "<b>Original A</b>", "云端 A1", "context-a1"),
                SyncReviewCandidate("a2", "file.json · ID 72", "Original A2", "<i>云端 A2</i>", "context-a2"),
            ),
        ),
        SyncReviewRow(
            "local-b",
            "key-b",
            "Original B",
            "本地 B",
            "other source.esp",
            "多个本地条目使用同一标识",
            (SyncReviewCandidate("b1", "another.json · ID 73", "Original B", "云端 B"),),
            can_select_remote=False,
            selection_disabled_reason="无法确定应更新哪条本地记录；可保留本地，本次跳过。",
        ),
        SyncReviewRow("local-c", "key-c", "Original C", "本地 C", "", "多个云端候选", _candidate_c()),
    )


def _candidate_c() -> tuple[SyncReviewCandidate, ...]:
    return (SyncReviewCandidate("c1", "file.json · ID 74", "Original C", "云端 C"),)


@pytest.fixture
def dialog(qapp):
    subject = SyncReviewDialog(_rows())
    subject.show()
    qapp.processEvents()
    yield subject
    subject.close()


def test_first_translation_is_visible_without_automatically_choosing_cloud_content(dialog):
    detail = dialog._detail
    assert dialog._selected_rows() == (0,)
    assert detail.local.original.toPlainText() == "<b>Original A</b>"
    assert detail.local.translation.toPlainText() == "<img>本地 A"
    assert detail.local.context.toPlainText() == "local source.esp"
    assert detail.candidates.currentData() == ""
    assert not detail.use_remote.isEnabled()
    assert not detail.undo.isEnabled()
    assert detail.remote.original.toPlainText() == ""
    assert "上方选择" in detail.remote.original.placeholderText()
    assert dialog.decisions() == {}


def test_identical_translations_with_different_statuses_are_distinguishable(qapp):
    first = SyncReviewCandidate("a", "ID 1", "Same original", "相同译文", stage=1)
    second = replace(first, candidate_id="b", label="ID 2", stage=5)
    row = replace(_rows()[0], stage=0, candidates=(first, second))
    subject = SyncReviewDialog((row,))
    detail = subject._detail
    assert detail.local.stage.text() == "翻译状态：未翻译"
    assert detail.local.context.accessibleName() == "补充说明（工程中已有）"
    assert detail.remote.context.accessibleName() == "补充说明（云端提供）"
    assert "已翻译" in detail.candidates.itemText(1)
    assert "已审核" in detail.candidates.itemText(2)
    detail.candidates.setCurrentIndex(1)
    assert detail.remote.stage.text() == "翻译状态：已翻译"
    detail.candidates.setCurrentIndex(2)
    assert detail.remote.stage.text() == "翻译状态：已审核"
    assert subject.decisions() == {}
    subject.close()


def test_equivalent_candidate_group_is_one_choice_with_explicit_upload_consequence(qapp):
    group = SyncReviewCandidate(
        "group", "相同译文（54 条云端记录）", "Same original", "相同译文", stage=1, source_count=54
    )
    subject = SyncReviewDialog((replace(_rows()[0], candidates=(group,)),))
    detail = subject._detail
    assert detail.candidates.count() == 2  # Placeholder and one content choice.
    assert "相同译文 ×54" in detail.candidates.itemText(1)
    detail.candidates.setCurrentIndex(1)
    assert "本次只采用共同译文" in detail.consequence.text()
    assert "54 条云端记录内容相同" in detail.consequence.text()
    assert "以后上传时" in detail.consequence.text()
    assert "需要另行确认" in detail.consequence.text()
    detail.use_remote.click()
    assert subject.decisions() == {"local-a": "group"}
    subject.close()


def test_candidate_requires_explicit_selection_and_apply_click(dialog):
    detail = dialog._detail
    detail.candidates.setCurrentIndex(2)
    assert detail.remote.original.toPlainText() == "Original A2"
    assert detail.remote.translation.toPlainText() == "<i>云端 A2</i>"
    assert detail.remote.context.toPlainText() == "context-a2"
    assert dialog.decisions() == {}
    detail.use_remote.click()
    assert dialog.decisions() == {"local-a": "a2"}
    assert dialog._model.data(dialog._model.index(0, 4)) == "已选择云端译文"
    assert "尚未执行" in detail.choice.text()
    assert detail.undo.isEnabled()


@pytest.mark.parametrize("kind", [OperationKind.DOWNLOAD, OperationKind.UPLOAD])
def test_previewing_another_cloud_entry_keeps_and_names_the_existing_choice(qapp, kind):
    subject = SyncReviewDialog(_rows(), kind=kind)
    detail = subject._detail
    detail.candidates.setCurrentIndex(1)
    detail.use_remote.click()
    assert subject.decisions() == {"local-a": "a1"}
    detail.candidates.setCurrentIndex(2)
    assert detail.remote.translation.toPlainText() == "<i>云端 A2</i>"
    assert subject.decisions() == {"local-a": "a1"}
    assert "当前选择仍是第 1 条" in detail.choice.text()
    assert "查看其他内容" in detail.choice.text()
    detail.use_remote.click()
    assert subject.decisions() == {"local-a": "a2"}
    assert "已选择第 2 条" in detail.choice.text()
    assert "当前选择仍是第 1 条" not in detail.choice.text()
    subject.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_enter_in_search_does_not_finish_or_cancel_the_review_or_lose_choices(dialog, qapp, key):
    dialog._detail.candidates.setCurrentIndex(1)
    dialog._detail.use_remote.click()
    finished = []
    dialog.finished.connect(finished.append)
    dialog._search.setFocus()
    QTest.keyClicks(dialog._search, "Original")
    QTest.keyClick(dialog._search, key)
    qapp.processEvents()
    assert dialog.isVisible()
    assert finished == []
    assert dialog.decisions() == {"local-a": "a1"}
    assert dialog._search.text() == "Original"


def test_cloud_choices_show_content_and_scene_with_identifiers_in_collapsed_details(dialog):
    detail = dialog._detail
    label = detail.candidates.itemText(2)
    assert "context-a2" in label
    assert "<i>云端 A2</i>" in label
    assert "ID 72" not in label
    assert not detail.details_toggle.isChecked()
    assert detail.details.isHidden()
    detail.candidates.setCurrentIndex(2)
    assert "key-a" in detail.details.toPlainText()
    assert "ID 72" in detail.details.toPlainText()
    assert detail.details.isHidden()
    detail.details_toggle.click()
    assert detail.details.isVisible()
    assert dialog.decisions() == {}
    dialog._table.selectRow(2)
    assert not detail.details_toggle.isChecked()
    assert detail.details.isHidden()
    assert "ID 72" not in detail.details.toPlainText()


def test_local_and_remote_fields_render_markup_as_literal_text(dialog):
    detail = dialog._detail
    detail.candidates.setCurrentIndex(1)
    assert detail.local.original.toPlainText() == "<b>Original A</b>"
    assert detail.local.translation.toPlainText() == "<img>本地 A"
    assert detail.remote.original.toPlainText() == "<b>Original A</b>"
    detail.candidates.setCurrentIndex(2)
    assert detail.remote.translation.toPlainText() == "<i>云端 A2</i>"
    assert detail.reason.textFormat() is Qt.TextFormat.PlainText
    for card in (detail.local, detail.remote):
        assert all(field.isReadOnly() for field in (card.original, card.translation, card.context))


def test_disabled_remote_selection_still_allows_comparison_and_keep(dialog):
    dialog._table.selectRow(1)
    detail = dialog._detail
    detail.candidates.setCurrentIndex(1)
    assert detail.remote.translation.toPlainText() == "云端 B"
    assert "无法确定" in detail.reason.text()
    assert not detail.use_remote.isEnabled()
    detail.use_remote.click()
    assert dialog.decisions() == {}
    detail.keep.click()
    assert dialog.decisions() == {"local-b": KEEP_LOCAL}


def test_bulk_keep_only_affects_selected_filtered_rows_and_can_be_reset(dialog):
    dialog._reason_filter.setCurrentIndex(dialog._reason_filter.findData("多个云端候选"))
    assert dialog._proxy.rowCount() == 2
    dialog._select_all.click()
    assert not dialog._detail.use_remote.isEnabled()
    assert not dialog._detail.comparison.isVisible()
    assert "2 条" in dialog._detail.keep.text()
    dialog._detail.keep.click()
    assert dialog.decisions() == {"local-a": KEEP_LOCAL, "local-c": KEEP_LOCAL}
    dialog._detail.undo.click()
    assert dialog.decisions() == {}


def test_search_matches_candidate_source_and_context(dialog):
    dialog._search.setText("CONTEXT-A2")
    assert dialog._proxy.rowCount() == 1
    assert dialog._proxy.data(dialog._proxy.index(0, 0)) == "key-a"
    dialog._search.setText("ID 73")
    assert dialog._proxy.rowCount() == 1
    assert dialog._proxy.data(dialog._proxy.index(0, 0)) == "key-b"


def test_bulk_keep_with_pending_filter_does_not_skip_rows_as_selection_changes(dialog):
    dialog._state_filter.setCurrentIndex(dialog._state_filter.findData("pending"))
    dialog._select_all.click()
    dialog._detail.keep.click()
    assert dialog.decisions() == {row.row_id: KEEP_LOCAL for row in _rows()}
    assert dialog._proxy.rowCount() == 0
    assert not dialog._detail.keep.isEnabled()
    assert not dialog._detail.use_remote.isEnabled()
    assert not dialog._detail.next.isEnabled()


def test_multi_selection_only_applies_keep_to_chosen_rows(dialog):
    selection = dialog._table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    selection.select(dialog._proxy.index(0, 0), flags)
    selection.select(dialog._proxy.index(2, 0), flags)
    dialog._detail.keep.click()
    assert dialog.decisions() == {"local-a": KEEP_LOCAL, "local-c": KEEP_LOCAL}


def test_reopening_preserves_explicit_candidate_but_cancel_does_not_mutate_input(qapp):
    rows = (replace(_rows()[0], decision="a2"),)
    subject = SyncReviewDialog(rows)
    assert subject._detail.candidates.currentData() == "a2"
    subject._detail.undo.click()
    assert subject.decisions() == {}
    subject._detail.keep.click()
    assert subject.decisions() == {"local-a": KEEP_LOCAL}
    subject.reject()
    assert subject.result() == QDialog.DialogCode.Rejected
    assert rows[0].decision == "a2"
    subject.close()


@pytest.mark.parametrize("pending_only", [False, True])
def test_thousands_of_rows_use_model_and_fixed_controls_with_one_batch_notification(qapp, pending_only):
    template = _rows()[0]
    subject = SyncReviewDialog(tuple(replace(template, row_id=f"row-{index}") for index in range(2181)))
    assert subject._model.rowCount() == 2181
    assert len(subject.findChildren(QComboBox)) == 3
    if pending_only:
        subject._state_filter.setCurrentIndex(subject._state_filter.findData("pending"))
    subject._select_all.click()
    data_changes, selection_changes = [], []
    subject._model.dataChanged.connect(lambda *_args: data_changes.append(True))
    subject._table.selectionModel().selectionChanged.connect(lambda *_args: selection_changes.append(True))
    subject._detail.keep.click()
    assert len(subject.decisions()) == 2181
    assert data_changes == [True]
    assert not selection_changes
    assert subject._proxy.rowCount() == (0 if pending_only else 2181)
    assert subject._detail.keep.isEnabled() is not pending_only
    subject.close()


def test_unavailable_candidate_can_be_compared_but_only_available_candidate_can_be_applied(qapp):
    row = _rows()[0]
    unavailable = replace(row.candidates[0], selectable=False, disabled_reason="此候选已绑定到另一条本地记录")
    subject = SyncReviewDialog((replace(row, candidates=(unavailable, row.candidates[1])),))
    detail = subject._detail
    detail.candidates.setCurrentIndex(1)
    assert "仅供查看" in detail.candidates.currentText()
    assert detail.remote.translation.toPlainText() == "云端 A1"
    assert "此候选已绑定" in detail.reason.text()
    assert "无法更新" in detail.consequence.text()
    assert not detail.use_remote.isEnabled()
    detail.use_remote.click()
    assert subject.decisions() == {}
    with pytest.raises(ValueError, match="not available"):
        subject._model.set_decision((0,), unavailable.candidate_id)
    detail.candidates.setCurrentIndex(2)
    assert detail.use_remote.isEnabled()
    assert "此候选已绑定" not in detail.reason.text()
    assert "无法更新" not in detail.consequence.text()
    detail.use_remote.click()
    assert subject.decisions() == {"local-a": "a2"}
    subject.close()


def test_unavailable_previous_choice_is_not_restored(qapp):
    row = _rows()[0]
    unavailable = replace(row.candidates[0], selectable=False, disabled_reason="重复远端 ID")
    subject = SyncReviewDialog((replace(row, candidates=(unavailable,), decision=unavailable.candidate_id),))
    assert subject.decisions() == {}
    assert subject._detail.candidates.currentData() == ""
    assert not subject._detail.use_remote.isEnabled()
    subject.close()


def test_upload_review_chooses_target_without_implicitly_overwriting_cloud(qapp):
    subject = SyncReviewDialog(_rows(), kind=OperationKind.UPLOAD)
    detail = subject._detail
    assert detail.candidates.currentData() == ""
    assert not detail.use_remote.isEnabled()
    assert detail.use_remote.text() == "用我的译文更新这条"
    assert "ParaTranz 的原文和补充说明保持原样" in detail.consequence.text()
    assert subject._model.data(subject._model.index(0, 4)) == "还没决定"
    detail.candidates.setCurrentIndex(2)
    assert subject.decisions() == {}
    detail.use_remote.click()
    assert subject.decisions() == {"local-a": "a2"}
    assert subject._model.data(subject._model.index(0, 4)) == "已选择上传我的译文"
    assert "确认上传才会生效" in subject._footer_note.text()
    subject.close()


def test_upload_bulk_keep_remote_only_applies_to_filtered_rows_and_can_be_deferred(qapp):
    subject = SyncReviewDialog(_rows(), kind=OperationKind.UPLOAD)
    subject._reason_filter.setCurrentIndex(subject._reason_filter.findData("多个云端候选"))
    subject._state_filter.setCurrentIndex(subject._state_filter.findData("pending"))
    subject._select_all.click()
    assert subject._detail.keep.text() == "这 2 条都先不上传"
    subject._detail.keep.click()
    assert subject.decisions() == {"local-a": KEEP_REMOTE, "local-c": KEEP_REMOTE}
    assert subject._proxy.rowCount() == 0
    subject._state_filter.setCurrentIndex(subject._state_filter.findData("decided"))
    subject._select_all.click()
    subject._detail.undo.click()
    assert subject.decisions() == {}
    subject.close()


def test_upload_rejects_download_retention_and_disabled_cloud_targets(qapp):
    row = replace(_rows()[1], decision=KEEP_LOCAL)
    subject = SyncReviewDialog((row,), kind=OperationKind.UPLOAD)
    assert subject.decisions() == {}
    with pytest.raises(ValueError, match="not available"):
        subject._model.set_decision((0,), KEEP_LOCAL)
    subject._detail.candidates.setCurrentIndex(1)
    assert not subject._detail.use_remote.isEnabled()
    subject._detail.keep.click()
    assert subject.decisions() == {"local-b": KEEP_REMOTE}
    subject.close()


@pytest.mark.parametrize(
    ("kind", "choice", "verb"),
    [(OperationKind.DOWNLOAD, KEEP_LOCAL, "更新"), (OperationKind.UPLOAD, KEEP_REMOTE, "上传")],
)
def test_missing_cloud_content_explains_the_gap_and_still_allows_skip(qapp, kind, choice, verb):
    row = replace(_rows()[0], candidates=(), reason="没有找到原来关联的云端内容。")
    subject = SyncReviewDialog((row,), kind=kind)
    detail = subject._detail
    assert "没有找到" in detail.heading.text()
    assert detail.candidates.currentText() == "没有可选内容"
    assert not detail.candidates.isEnabled()
    assert not detail.use_remote.isEnabled()
    assert f"无法{verb}" in detail.consequence.text()
    assert detail.remote.original.placeholderText() == "找不到对应内容"
    assert detail.keep.text() == f"这条先不{verb}"
    detail.keep.click()
    assert subject.decisions() == {row.row_id: choice}
    detail.undo.click()
    assert subject.decisions() == {}
    subject.close()


@pytest.mark.parametrize(
    ("kind", "choice", "verb"),
    [(OperationKind.DOWNLOAD, KEEP_LOCAL, "更新"), (OperationKind.UPLOAD, KEEP_REMOTE, "上传")],
)
def test_all_cloud_entries_unavailable_explains_inability_to_sync_but_allows_skip(qapp, kind, choice, verb):
    row = _rows()[0]
    unavailable = tuple(
        replace(candidate, selectable=False, disabled_reason="这条云端内容已经用于另一条翻译。")
        for candidate in row.candidates
    )
    subject = SyncReviewDialog((replace(row, candidates=unavailable),), kind=kind)
    detail = subject._detail
    assert detail.heading.text() == "这些云端内容暂时不能使用"
    assert "哪一条" not in detail.heading.text()
    assert f"无法{verb}" in detail.consequence.text()
    assert not detail.use_remote.isEnabled()
    assert detail.keep.isEnabled()
    detail.candidates.setCurrentIndex(1)
    assert detail.remote.translation.toPlainText() == "云端 A1"
    assert f"无法{verb}" in detail.consequence.text()
    assert not detail.use_remote.isEnabled()
    assert "已经用于另一条翻译" in detail.reason.text()
    detail.keep.click()
    assert subject.decisions() == {row.row_id: choice}
    subject.close()


@pytest.mark.parametrize(
    ("kind", "preserves_local_source", "expected"),
    [
        (OperationKind.DOWNLOAD, True, "原文和补充说明保持原样，不会写回 ESM/ESP"),
        (OperationKind.DOWNLOAD, False, "原文和补充说明保持原样，不会写回 ESM/ESP"),
        (OperationKind.UPLOAD, True, "ParaTranz 的原文和补充说明保持原样"),
        (OperationKind.UPLOAD, False, "ParaTranz 的原文和补充说明保持原样"),
    ],
)
def test_consequence_matches_operation_and_local_source_preservation(qapp, kind, preserves_local_source, expected):
    row = replace(_rows()[0], preserves_local_source=preserves_local_source)
    subject = SyncReviewDialog((row,), kind=kind)
    assert expected in subject._detail.consequence.text()
    subject._detail.candidates.setCurrentIndex(2)
    assert expected in subject._detail.consequence.text()
    assert subject.decisions() == {}
    subject.close()


def test_next_undecided_skips_choices_wraps_and_does_not_make_decisions(dialog):
    dialog._table.selectRow(1)
    dialog._detail.keep.click()
    dialog._table.selectRow(0)
    dialog._detail.next.click()
    assert dialog._selected_rows() == (2,)
    assert dialog._detail.local.translation.toPlainText() == "本地 C"
    dialog._detail.next.click()
    assert dialog._selected_rows() == (0,)
    assert dialog.decisions() == {"local-b": KEEP_LOCAL}


def test_next_undecided_respects_filters_and_never_applies_to_another_row(dialog):
    dialog._reason_filter.setCurrentIndex(dialog._reason_filter.findData("多个云端候选"))
    dialog._state_filter.setCurrentIndex(dialog._state_filter.findData("pending"))
    dialog._detail.candidates.setCurrentIndex(2)
    dialog._detail.use_remote.click()
    assert dialog.decisions() == {"local-a": "a2"}
    assert dialog._proxy.rowCount() == 1
    dialog._detail.next.click()
    assert dialog._selected_rows() == (2,)
    assert dialog._detail.candidates.currentData() == ""
    assert not dialog._detail.use_remote.isEnabled()
    assert not dialog._detail.next.isEnabled()
    assert dialog.decisions() == {"local-a": "a2"}
    dialog._detail.keep.click()
    assert dialog.decisions() == {"local-a": "a2", "local-c": KEEP_LOCAL}
    assert dialog._proxy.rowCount() == 0
    assert not dialog._detail.next.isEnabled()


def test_finished_choices_return_to_main_confirmation_with_explicit_staged_status(dialog):
    dialog._detail.keep.click()
    assert "尚未执行" in dialog._footer_note.text()
    finish = dialog._buttons.button(QDialogButtonBox.StandardButton.Apply)
    assert finish.text() == "完成选择，返回更新"
    finish.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.decisions() == {"local-a": KEEP_LOCAL}
