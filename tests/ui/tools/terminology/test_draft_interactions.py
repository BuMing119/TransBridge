from types import SimpleNamespace

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from transbridge.application.terminology.conflicts import ConflictResolutionOperation
from transbridge.application.terminology.models import DecisionStatus, TermDecision
from transbridge.ui.tools.terminology.conflict_review_dialog import ConflictReviewDialog
from transbridge.ui.tools.terminology.draft_interactions import DraftInteractionController
from transbridge.ui.tools.terminology.term_edit_dialog import TermEditDialog
from transbridge.ui.tools.terminology.window import _decision_status_label

_APP = QApplication.instance() or QApplication([])


def _details(count=2):
    return SimpleNamespace(
        conflict=SimpleNamespace(
            normalized_original="Dragon",
            variants=(
                SimpleNamespace(normalized_translation="巨龙", evidence_ids=("a",)),
                SimpleNamespace(normalized_translation="飞龙", evidence_ids=("b",)),
            ),
        ),
        decisions=(),
        plugin_ids=("Main.esm", "Other.esp"),
        evidence=tuple(
            SimpleNamespace(
                original="Dragon",
                translation="巨龙",
                plugin_scope="Main.esm",
                source_chain=("Source.esm",),
                entry_key=f"entry-{index}",
                context="完整上下文",
            )
            for index in range(count)
        ),
    )


def test_postponing_a_conflict_does_not_modify_the_draft():
    calls = []
    controller = DraftInteractionController(
        QWidget(),
        SimpleNamespace(conflict_details=lambda _conflict: _details()),
        calls.append,
        lambda call, success, _message: success(call()),
    )
    QTimer.singleShot(0, lambda: _APP.activeModalWidget().reject())
    controller.review(object())
    assert calls == []


def test_excluding_a_conflict_is_an_explicit_draft_mutation(monkeypatch):
    calls = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    details = _details()
    presenter = SimpleNamespace(
        conflict_details=lambda _conflict: details, resolve_review=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    controller = DraftInteractionController(
        QWidget(), presenter, lambda command: command(), lambda call, success, _message: success(call())
    )

    def exclude():
        dialog = _APP.activeModalWidget()
        dialog.operation.setCurrentIndex(2)
        dialog._save()

    QTimer.singleShot(0, exclude)
    controller.review(object())
    assert calls == [((details, ConflictResolutionOperation.IGNORE), {"translation": None, "plugin_id": None})]


def test_conflict_dialog_shows_full_evidence_and_selects_actual_plugin():
    details = _details(503)
    dialog = ConflictReviewDialog(details)
    model = dialog.evidence.model()
    assert model.rowCount() == 503
    assert model.index(502, 4).data() == "entry-502"
    assert model.index(502, 5).data(Qt.ItemDataRole.ToolTipRole) == "完整上下文"
    dialog.operation.setCurrentIndex(1)
    dialog.plugin.setCurrentText("Other.esp")
    dialog.translation.setCurrentText("飞龙")
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.choice.plugin_id == "Other.esp"
    assert dialog.choice.translation == "飞龙"


def test_confirming_unchanged_translation_resolves_review_required(monkeypatch):
    calls = []
    decision = TermDecision("dragon", "project", "variant", "Dragon", "dragon", "巨龙")
    draft = SimpleNamespace(ref="draft-1", decisions=(decision,))
    presenter = SimpleNamespace(
        term_editing=SimpleNamespace(
            prepare_draft=lambda: draft,
            edit_term=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
    )

    def save(dialog):
        dialog.save_button.click()
        return dialog.result()

    monkeypatch.setattr(TermEditDialog, "exec", save)
    controller = DraftInteractionController(
        QWidget(),
        presenter,
        lambda command, on_saved, on_error: on_saved(command()),
        lambda command, on_ready, _message: on_ready(command()),
    )
    controller.edit(decision)
    assert calls[0][0] == ("dragon",)
    assert calls[0][1]["translation"] == "巨龙"
    assert calls[0][1]["expected_ref"] == "draft-1"
    assert _decision_status_label(decision) == "待复核"
    assert _decision_status_label(SimpleNamespace(status=DecisionStatus.MANUAL_CONFIRMED)) == "已确认 · 待发布"


def test_review_keeps_existing_plugin_choice_and_names_suppressed_decision():
    details = _details()
    details.conflict.status = "plugin_exception"
    details.decisions = (
        SimpleNamespace(scope=SimpleNamespace(plugin_id=None), translation="龙", suppressed=True),
        SimpleNamespace(scope=SimpleNamespace(plugin_id="Other.esp"), translation="特例译名", suppressed=False),
    )
    dialog = ConflictReviewDialog(details)
    assert "整个工程：已停用" in dialog.current_label.text()
    assert dialog.operation.currentData() is ConflictResolutionOperation.PLUGIN_EXCEPTION
    assert dialog.plugin.currentText() == "Other.esp"
    assert dialog.translation.currentText() == "特例译名"
    dialog.keep_button.click()
    assert dialog.choice.operation is ConflictResolutionOperation.PLUGIN_EXCEPTION
    assert dialog.choice.translation == "特例译名"
    assert dialog.choice.plugin_id == "Other.esp"
