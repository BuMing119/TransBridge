from dataclasses import replace

from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication

from tests.ui.tools.terminology.test_conflict_evidence_panel import make_build, review_details, review_window
from tests.ui.tools.terminology.test_conflict_filters import _settle
from tests.ui.tools.terminology.test_draft_interactions import _details
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.persistence.terminology import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.build_projection import project_build
from transbridge.ui.tools.terminology.conflict_review_pane import ConflictReviewPane

_APP = QApplication.instance() or QApplication([])


def test_equivalent_hidden_by_default_but_can_inspect_reason_and_edit(tmp_path):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    build = make_build()
    equivalent = replace(
        build.conflicts[0], status="equivalent", recommended_translation="巨龙", notes="正常表达差异，核心术语一致。"
    )
    build = replace(build, conflicts=(equivalent,))
    repository.put_build(build)
    window = review_window(repository, lambda group, context, **kwargs: review_details(build, group))
    window._preflight_started = True
    try:
        window.bind_page("conflicts", build.ref)
        _settle(window.conflicts_model)
        assert window.conflicts_model.rowCount() == 0
        window.terms_view.judgment_history_button.click()
        _settle(window.conflicts_model)
        assert window.conflicts_model.rowCount() == 1
        assert window.conflicts_model.index(0, 3).data() == "同一术语 · 无需处理"
        pane = window.conflicts_view.pane
        _wait(lambda: pane.details is not None)
        assert "核心术语一致" in pane.judgment_text.toPlainText()
        assert pane.judgment_text.isHidden()
        pane.judgment_toggle.click()
        assert not pane.judgment_text.isHidden()
        pane.candidate_buttons[1].click()
        assert pane.save_button.isEnabled()
        assert pane.translation.text() == "飞龙"
        pane.saved()
        window.conflicts_view.reset_filters()
        _settle(window.conflicts_model)
        assert window.conflicts_view.review_state == "equivalent"
        window.conflicts_view.tabs.setCurrentIndex(0)
        _settle(window.conflicts_model)
        assert window.conflicts_model.rowCount() == 0
    finally:
        window.close()
        QThreadPool.globalInstance().waitForDone(3000)
        repository.close()


def test_equivalent_review_displays_reason_without_implicitly_choosing_manual_override():
    details = _details()
    details.conflict.status = "equivalent"
    details.conflict.notes = "同一术语的表达差异"
    details.conflict.recommended_translation = "飞龙"
    pane = ConflictReviewPane()
    try:
        pane.set_details(details)
        assert "同一术语" in pane.judgment_text.toPlainText()
        assert not pane.translation.text()
        assert not any(button.isChecked() for button in pane.candidate_buttons)
        assert not pane.save_button.isEnabled()
    finally:
        pane.deleteLater()


def test_build_summary_distinguishes_filtered_and_uncertain_groups():
    build = replace(
        make_build(),
        diagnostics=(
            "CONFLICT_JUDGMENT_COMPLETED:3",
            "CONFLICT_JUDGMENT_EQUIVALENT:1",
            "CONFLICT_JUDGMENT_UNCERTAIN:1",
        ),
    )
    view = project_build(build)
    assert "LLM 已判断 3 组" in view.decisions
    assert "1 组为同一术语" in view.decisions
    assert "1 组无法确定" in view.decisions
