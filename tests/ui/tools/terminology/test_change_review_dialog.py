from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from tests.ui.tools.terminology.search_test_support import wait_search
from transbridge.application.terminology import Page
from transbridge.application.terminology.models import (
    CanonicalChange,
    CanonicalDiff,
    ChangeType,
    DecisionStatus,
    TermDecision,
    TermScope,
)
from transbridge.ui.tools.terminology.change_review_dialog import CanonicalDiffView, ChangeReviewDialog
from transbridge.ui.tools.terminology.history_view import HistoryView
from transbridge.ui.tools.terminology.paged_models import KeysetPagedTableModel, PagedColumn

_APP = QApplication.instance() or QApplication([])


def _decision(index=0):
    return TermDecision(
        f"term-{index}",
        "project",
        "variant",
        f"Dragon {index}",
        f"dragon {index}",
        f"龙 {index}",
        status=DecisionStatus.ADOPTED,
    )


def _diff(changes):
    return CanonicalDiff("old-version", "new-version", "digest", tuple(changes))


def test_review_shows_translation_scope_status_and_full_details():
    before = _decision()
    after = replace(
        before,
        translation="远古巨龙",
        scope=TermScope.plugin("Skyrim.esm"),
        status=DecisionStatus.MANUAL_CONFIRMED,
        notes="保留这一段完整备注" * 20,
        evidence_ids=("source-evidence",),
    )
    change = CanonicalChange(
        "change", ChangeType.TRANSLATION_CHANGED, before.term_id, "old", "new", True, before, after
    )
    dialog = ChangeReviewDialog(_diff([change]))
    values = [dialog.changes.filtered.index(0, col).data() for col in range(8)]
    assert values == ["调整译名", "Dragon 0", "龙 0", "远古巨龙", "整个工程", "插件：Skyrim.esm", "已采用", "人工确认"]
    assert after.notes in dialog.changes.details.toPlainText()
    assert "source-evidence" in dialog.changes.details.toPlainText()
    assert "old-version → new-version" in dialog.changes.summary.text()
    assert dialog.publish_button.text() == "确认发布"
    assert not dialog.changes.model.flags(dialog.changes.model.index(0, 0)) & Qt.ItemFlag.ItemIsEditable
    dialog.close()


def test_all_changes_are_searchable_including_rows_after_500_and_hidden_notes():
    changes = []
    for index in range(605):
        after = replace(_decision(index), notes="尾部审阅证据" if index == 604 else "")
        changes.append(
            CanonicalChange(f"change-{index:04d}", ChangeType.ADDED, after.term_id, None, "new", after=after)
        )
    view = CanonicalDiffView()
    view.set_comparison(_diff(changes))
    assert view.filtered.rowCount() == 605
    view.search.setText("尾部审阅证据")
    wait_search(view._search)
    assert view.filtered.rowCount() == 1
    assert view.filtered.index(0, 1).data() == "Dragon 604"
    assert view.count.text() == "显示 1 / 605 项变化"
    view.search.setText("does-not-exist")
    wait_search(view._search)
    assert view.filtered.rowCount() == 0
    assert "没有匹配" in view.details.toPlainText()
    view.search.clear()
    assert view.filtered.rowCount() == 605
    view.table.setCurrentIndex(view.filtered.index(604, 0))
    assert "Dragon 604" in view.details.toPlainText()
    view.search.setText("Dragon")
    wait_search(view._search)
    assert "Dragon 604" in view.details.toPlainText()
    view.close()


def test_missing_sides_and_conflict_status_changes_have_explicit_meaning():
    term = _decision()
    removed = CanonicalChange("removed", ChangeType.SUPPRESSED, term.term_id, "old", None, before=term)
    conflict = CanonicalChange(
        "conflict",
        ChangeType.CONFLICT_STATUS_CHANGED,
        "conflict-id",
        None,
        None,
        details=(("before_status", "unresolved"), ("after_status", "unified")),
    )
    view = CanonicalDiffView()
    view.set_comparison(_diff([removed, conflict]))
    assert view.filtered.index(0, 0).data() == "冲突处理变化"
    assert view.filtered.index(0, 6).data() == "待决定"
    assert view.filtered.index(0, 7).data() == "已统一"
    assert "新冲突状态：已统一" in view.details.toPlainText()
    assert view.filtered.index(1, 0).data() == "移除"
    assert view.filtered.index(1, 3).data() == "—"
    assert view.filtered.index(1, 7).data() == "不存在"
    view.set_comparison(_diff([]))
    assert view.filtered.rowCount() == 0
    assert "没有变化" in view.details.toPlainText()
    view.close()


def test_history_comparison_displays_actual_values_and_can_search():
    model = KeysetPagedTableModel(lambda _ref, _request: Page((), "snapshot"), (PagedColumn("version", "版本", str),))
    view = HistoryView(model)
    term = _decision()
    change = CanonicalChange("added", ChangeType.ADDED, term.term_id, None, "new", after=term)
    view.set_comparison(_diff([change]))
    assert not view.comparison.isHidden()
    assert view.comparison.filtered.index(0, 3).data() == "龙 0"
    assert "1 项变化" in view.status.text()
    view.comparison.search.setText("Dragon")
    assert view.comparison.filtered.rowCount() == 1
    model.close()
    view.close()
