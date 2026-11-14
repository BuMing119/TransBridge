"""Explicit candidate choices preserve custom input and task scope."""

from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from tests.application.terminology.test_input_capture import (
    _Baselines,
    _Capabilities,
    _Leases,
    _Projects,
    _registry,
    _state,
)
from tests.ui.tools.terminology.test_draft_interactions import _details
from tests.ui.tools.terminology.test_workflow_routes import _wait
from transbridge.application.contracts import RequestContext
from transbridge.application.projects.source_registry import SourceRegistrySnapshot
from transbridge.application.terminology.input_capture import BuildInputCaptureService
from transbridge.ui.tools.terminology.conflict_review_dialog import ConflictReviewDialog
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.window import TerminologyWindow

_APP = QApplication.instance() or QApplication([])


def test_review_reads_complete_evidence_and_keeps_advanced_options_collapsed():
    details = _details()
    dialog = ConflictReviewDialog(details)
    try:
        assert dialog.advanced.isHidden()
        assert dialog.original_text.toPlainText() == details.evidence[0].original
        assert dialog.translation_text.toPlainText() == details.evidence[0].translation
        for row, item in enumerate(details.evidence):
            dialog.evidence.selectRow(row)
            assert dialog.original_text.toPlainText() == item.original
            assert dialog.translation_text.toPlainText() == item.translation
        dialog.advanced_toggle.click()
        assert not dialog.advanced.isHidden()
        dialog.resize(4000, 3000)
        dialog.show()
        _APP.processEvents()
        assert dialog.screen().availableGeometry().contains(dialog.frameGeometry())
        assert dialog.buttons.isVisible()
    finally:
        dialog.deleteLater()


def test_candidate_click_submits_the_visible_translation_and_custom_text_clears_selection():
    dialog = ConflictReviewDialog(_details())
    saved = []
    dialog.save_requested.connect(saved.append)
    try:
        dialog.candidate_buttons[1].click()
        assert dialog.translation.currentText() == "飞龙"
        assert dialog.candidate_buttons[1].isChecked()
        dialog.translation.setCurrentText("自定义译名")
        assert not any(button.isChecked() for button in dialog.candidate_buttons)
        dialog.candidate_buttons[0].click()
        dialog._save()
        assert saved[0].translation == "巨龙"
        assert all(not button.isEnabled() for button in dialog.candidate_buttons)
        dialog.saved()
    finally:
        dialog.deleteLater()


def test_recheck_keeps_all_enabled_project_plugins_when_only_one_is_loaded():
    first = _registry().sources[0]
    second = replace(first, source_id="second", location="C:/mods/second.esp")
    disabled = replace(first, source_id="disabled", enabled=False)
    inputs = BuildInputCaptureService(
        _Projects(_state(SourceRegistrySnapshot((first, second, disabled)))),
        _Leases(),
        _Capabilities(),
        _Baselines(),
    )
    presenter = TerminologyPresenter(
        TerminologyUiServices(build_inputs=inputs),
        RequestContext("operator", project_id="project-1", variant_id="main"),
    )
    window = TerminologyWindow(presenter)
    window._ui_context = SimpleNamespace(
        slots={"loaded": SimpleNamespace(source_id=first.source_id, collection=(), source_snapshot=object())}
    )
    try:
        window.run_preflight(repair=True, refresh_assets=False)
        _wait(lambda: presenter.snapshot is not None)
        assert {source.registration.source_id for source in presenter.snapshot.sources} == {first.source_id, "second"}
        assert "source_ids" not in presenter._build_config
        _wait(lambda: window.workspace.project_caption.toolTip() == "2 个来源")
    finally:
        window.close()
        window._actions._pool.waitForDone(3000)
