"""Direct conflict review makes choice deliberate and evidence readable."""

from types import SimpleNamespace

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from transbridge.application.terminology.conflicts import ConflictResolutionOperation
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.ui.tools.terminology.conflict_review_pane import ConflictReviewPane

_APP = QApplication.instance() or QApplication([])


def _details(status="unresolved", decisions=()):
    evidence = tuple(
        SimpleNamespace(
            evidence_id=str(index),
            original=f"Dragon original {index}",
            translation=f"译文 {index}",
            plugin_scope="Main.esm",
            source_chain=("Source.esm",),
            entry_key=f"entry-{index}",
            context="BOOK:DESC",
        )
        for index in range(3)
    )
    return SimpleNamespace(
        conflict=SimpleNamespace(
            normalized_original="Dragon",
            status=status,
            notes="同一原名存在竞争译名。",
            variants=(
                SimpleNamespace(normalized_translation="巨龙", evidence_ids=("0", "1")),
                SimpleNamespace(normalized_translation="飞龙", evidence_ids=("2",)),
            ),
        ),
        decisions=decisions,
        plugin_ids=("Main.esm", "Other.esp"),
        evidence=evidence,
    )


def test_undecided_does_not_preselect_and_candidate_switches_full_examples():
    pane = ConflictReviewPane()
    try:
        pane.set_details(_details())
        assert not any(button.isChecked() for button in pane.candidate_buttons)
        assert not pane.save_button.isEnabled()
        assert not pane.has_unsaved_inputs
        pane.candidate_buttons[0].click()
        assert pane.translation.text() == "巨龙"
        assert "2 次" in pane.candidate_buttons[0].text()
        assert pane.original_text.toPlainText() == "Dragon original 0"
        pane.next_example.click()
        assert pane.original_text.toPlainText() == "Dragon original 1"
        assert "entry-1" in pane.source_label.toolTip()
        pane.candidate_buttons[1].click()
        assert pane.translation_text.toPlainText() == "译文 2"
        assert not pane.next_example.isEnabled()
        assert pane.has_unsaved_inputs
    finally:
        pane.deleteLater()


def test_resolved_choice_custom_save_and_duplicate_submission_guard():
    decision = TermDecision("dragon", "project", "variant", "Dragon", "dragon", "飞龙")
    pane = ConflictReviewPane()
    saved = []
    pane.save_requested.connect(saved.append)
    try:
        pane.set_details(_details("unified", (decision,)))
        assert pane.candidate_buttons[1].isChecked()
        assert pane.save_button.text() == "保存修改"
        assert "飞龙" in pane.current_label.text()
        pane.custom_toggle.click()
        pane.translation.setText("自定义龙")
        assert not any(button.isChecked() for button in pane.candidate_buttons)
        pane.save_button.click()
        pane.save_button.click()
        assert len(saved) == 1
        assert saved[0].translation == "自定义龙"
        assert saved[0].operation is ConflictResolutionOperation.UNIFY
        assert not pane.skip_button.isEnabled()
        pane.set_error(RuntimeError("草稿已变化"))
        assert pane.has_unsaved_inputs
        assert pane.translation.text() == "自定义龙"
        assert pane.save_button.isEnabled()
        pane.saved()
        assert not pane.has_unsaved_inputs
    finally:
        pane.deleteLater()


def test_unresolved_decision_is_not_presented_as_adopted():
    decision = TermDecision(
        "dragon",
        "project",
        "variant",
        "Dragon",
        "dragon",
        "飞龙",
        status=DecisionStatus.UNRESOLVED,
    )
    pane = ConflictReviewPane()
    try:
        pane.set_details(_details(decisions=(decision,)))
        assert not pane.translation.text()
        assert not pane.save_button.isEnabled()
        pane.candidate_buttons[0].click()
        pane.invalidate_context()
        assert not pane.save_button.isEnabled()
        assert pane.has_unsaved_inputs
        assert pane.translation.text() == "巨龙"
    finally:
        pane.deleteLater()


def test_plugin_exception_is_explicit_and_preserved():
    pane = ConflictReviewPane()
    saved = []
    pane.save_requested.connect(saved.append)
    try:
        pane.set_details(_details())
        pane.candidate_buttons[1].click()
        pane.operation.setCurrentIndex(1)
        pane.plugin.setCurrentText("Other.esp")
        pane.save_button.click()
        assert saved[0].plugin_id == "Other.esp"
        assert saved[0].operation is ConflictResolutionOperation.PLUGIN_EXCEPTION
        pane.saved()
        pane.set_loading()
        assert pane.details is None
        assert not pane.save_button.isEnabled()
    finally:
        pane.deleteLater()


def test_long_text_stays_inside_small_pane_and_details_reset_disclosures():
    pane = ConflictReviewPane()
    details = _details()
    details.conflict.normalized_original = "Long original " * 100
    details.conflict.notes = "判断依据" * 1000
    details.conflict.variants[0].normalized_translation = "很长的译名" * 100
    details.evidence[0].original = "Full original " * 1000
    try:
        pane.set_details(details)
        pane.resize(520, 480)
        pane.show()
        _APP.processEvents()
        assert pane.width() == 520
        assert pane.height() == 480
        assert pane.save_button.geometry().right() < pane.width()
        pane.candidate_buttons[0].click()
        assert pane.original_text.toPlainText() == details.evidence[0].original
        assert pane.judgment_text.isHidden()
        pane.judgment_toggle.click()
        assert not pane.judgment_text.isHidden()
        pane.set_details(_details())
        assert pane.judgment_text.isHidden()
        assert not pane.has_unsaved_inputs
        assert pane.translation.text() == ""
    finally:
        pane.hide()
        pane.deleteLater()


def test_advanced_click_reveals_controls_inside_viewport_and_can_exclude(monkeypatch):
    pane = ConflictReviewPane()
    saved = []
    pane.save_requested.connect(saved.append)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    try:
        pane.set_details(_details())
        pane.resize(600, 480)
        pane.show()
        for _ in range(3):
            _APP.processEvents()
        pane.custom_toggle.click()
        for _ in range(3):
            _APP.processEvents()
        pane.scroll.ensureWidgetVisible(pane.advanced_toggle)
        QTest.mouseClick(pane.advanced_toggle, Qt.MouseButton.LeftButton)
        for _ in range(5):
            _APP.processEvents()
        viewport = pane.scroll.viewport()
        controls = QRect(pane.advanced.mapTo(viewport, QPoint()), pane.advanced.size())
        assert viewport.rect().contains(controls)
        assert "收起" in pane.advanced_toggle.text()
        assert pane.operation.isEnabled()
        pane.operation.setCurrentIndex(pane.operation.findData(ConflictResolutionOperation.IGNORE))
        assert pane.save_button.isEnabled()  # Excluding does not require choosing a translation.
        pane.save_button.click()
        assert saved[0].operation is ConflictResolutionOperation.IGNORE
        assert saved[0].translation is None
        pane.saved()
        pane.advanced_toggle.click()
        assert pane.advanced.isHidden()
    finally:
        pane.close()
        pane.deleteLater()
