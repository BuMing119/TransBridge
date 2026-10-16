from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from tests.ui.tools.terminology.search_test_support import wait_search
from transbridge.application.terminology.effective import EffectiveSnapshotStatus, EffectiveTerminologySnapshot
from transbridge.application.terminology.models import DecisionStatus, TermDecision, TermScope
from transbridge.application.terminology_profiles import (
    InMemoryTerminologyProfileRepository,
    TerminologyProfileImportService,
    TerminologyProfileService,
    TerminologySourceEntry,
    TerminologySourceSnapshot,
)
from transbridge.ui.tools.terminology_profiles import TerminologySourceImportDialog

_APP = QApplication.instance() or QApplication([])


def _preview():
    decisions = [
        TermDecision(
            f"term-{index}",
            "p",
            "v",
            f"Term {index:04d}",
            f"term {index:04d}",
            f"默认{index}",
            status=DecisionStatus.ADOPTED,
        )
        for index in range(610)
    ]
    decisions.append(
        TermDecision(
            "scoped",
            "p",
            "v",
            "Term 0609",
            "term 0609",
            "插件默认",
            TermScope.plugin("Last.esp"),
            DecisionStatus.ADOPTED,
        )
    )
    source = TerminologySourceSnapshot.capture(
        "json",
        "外部测试来源",
        (
            TerminologySourceEntry("Term 0607", "来源译名"),
            TerminologySourceEntry("Term 0608", "冲突甲"),
            TerminologySourceEntry("Term 0608", "冲突乙"),
            TerminologySourceEntry("Term 0609", "范围候选"),
            TerminologySourceEntry("Source Only", "不会导入的译名"),
        ),
    )
    base = EffectiveTerminologySnapshot(
        "p", "v", EffectiveSnapshotStatus.READY, version_id="base", content_digest="digest", decisions=tuple(decisions)
    )
    return TerminologyProfileImportService(TerminologyProfileService(InMemoryTerminologyProfileRepository())).preview(
        "p", "v", base, source
    )


def _filter(dialog, result):
    dialog.result_filter.setCurrentIndex(dialog.result_filter.findData(result))
    wait_search(dialog.filtered.search)


def test_import_preview_searches_after_row_500_and_preserves_creation_options():
    preview = _preview()
    dialog = TerminologySourceImportDialog(preview, "初始方案")
    dialog.name_edit.setText("手动填写的方案")
    dialog.select_check.setChecked(True)
    assert dialog.model.rowCount() == dialog.filtered.rowCount() == 612
    assert "前 500" not in dialog.summary_label.text()
    dialog.search_edit.setText("Term 0607")
    wait_search(dialog.filtered.search)
    assert dialog.filtered.rowCount() == 1
    assert dialog.filtered.index(0, 2).data() == "来源译名"
    assert dialog.filtered.index(0, 4).data() == "采用来源译名"
    dialog.search_edit.setText("冲突乙")
    _filter(dialog, "source_conflict")
    assert dialog.filtered.rowCount() == 1
    assert dialog.filtered.index(0, 0).data() == "Term 0608"
    assert "冲突甲" in dialog.row_details.toPlainText()
    assert "冲突乙" in dialog.row_details.toPlainText()
    assert "无法自动选择" in dialog.row_details.toPlainText()
    dialog.search_edit.clear()
    _filter(dialog, "all")
    assert dialog.filtered.rowCount() == 612
    assert dialog.profile_name == "手动填写的方案"
    assert dialog.select_after_create
    assert dialog.create_button.text() == "创建并选用"
    dialog.table.setCurrentIndex(dialog.filtered.index(611, 0))
    assert dialog.filtered.index(611, 0).data() == "Source Only"
    dialog.table.setCurrentIndex(dialog.filtered.index(0, 0))
    assert dialog.table.currentIndex().row() == 0
    assert not dialog.model.flags(dialog.model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
    dialog.close()


def test_diagnostic_filters_explain_scope_conflict_and_source_only_exclusion():
    dialog = TerminologySourceImportDialog(_preview(), "方案")
    _filter(dialog, "problems")
    assert dialog.filtered.rowCount() == 4
    _filter(dialog, "scope_ambiguous")
    assert dialog.filtered.rowCount() == 2
    text = dialog.row_details.toPlainText()
    assert "外部来源未指定适用范围，无法自动分配" in text
    assert "整个工程" in text and "Last.esp" in text
    assert "创建后管理译名方案并逐项调整" in text
    dialog.search_edit.setText("Last.esp")
    wait_search(dialog.filtered.search)
    assert dialog.filtered.rowCount() == 2  # Both rows describe the conflicting project/plugin scopes.
    dialog.search_edit.clear()
    _filter(dialog, "source_only")
    assert dialog.filtered.rowCount() == 1
    assert dialog.filtered.index(0, 2).data() == "未导入"
    assert "不会导入的译名" in dialog.row_details.toPlainText()
    assert "不会写入译名方案" in dialog.row_details.toPlainText()
    dialog.search_edit.setText("not present")
    wait_search(dialog.filtered.search)
    assert dialog.filtered.rowCount() == 0
    assert "没有匹配项" in dialog.row_details.toPlainText()
    assert dialog.create_button.isEnabled()  # A display filter never changes the creation payload.
    dialog.close()


def test_import_creation_requires_name_even_when_empty_at_construction():
    dialog = TerminologySourceImportDialog(_preview(), "")
    assert not dialog.create_button.isEnabled()
    dialog.name_edit.setText(" ")
    assert not dialog.create_button.isEnabled()
    dialog.name_edit.setText("新方案")
    assert dialog.create_button.isEnabled()
    dialog.close()


def test_import_background_search_preserves_selected_mapping_and_stops_on_reject():
    dialog = TerminologySourceImportDialog(_preview(), "方案")
    dialog.table.setCurrentIndex(dialog.filtered.index(600, 0))
    selected = dialog.table.currentIndex().data(Qt.ItemDataRole.UserRole)
    dialog.search_edit.setText("term")
    wait_search(dialog.filtered.search)
    assert dialog.table.currentIndex().data(Qt.ItemDataRole.UserRole) is selected
    dialog.search_edit.clear()
    assert dialog.table.currentIndex().data(Qt.ItemDataRole.UserRole) is selected
    dialog.search_edit.setText("pending search")
    assert dialog.filtered.search.is_pending
    dialog.reject()
    assert not dialog.filtered.search.is_pending
    assert dialog.filtered.search._jobs.closed.is_set()
