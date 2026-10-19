from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import QItemSelectionModel, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget
import pytest

from tests.application.terminology.test_build import _entry
from tests.integration.bootstrap.test_terminology_build_review import _Harness
from transbridge.application.terminology.models import DecisionStatus, ScopeKind, TermDecision, TermScope
from transbridge.persistence.terminology.repository import SqliteTerminologyRepository
from transbridge.ui.tools.terminology.draft_interactions import DraftInteractionController
from transbridge.ui.tools.terminology.draft_view import DraftView
from transbridge.ui.tools.terminology.presenter import TerminologyPresenter, TerminologyUiServices
from transbridge.ui.tools.terminology.term_edit_dialog import TermEditDialog

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def harness(tmp_path, monkeypatch):
    repository = SqliteTerminologyRepository.open(str(tmp_path), "project-1")
    try:
        yield _Harness(repository, monkeypatch)
    finally:
        repository.close()


def _controller(harness):
    presenter = TerminologyPresenter(TerminologyUiServices(commands=harness.commands), harness.context)

    def write(command, on_saved=None, on_error=None):
        try:
            draft = command()
        except Exception as error:
            if on_error is None:
                raise
            on_error(error)
        else:
            if on_saved is not None:
                on_saved(draft)

    return DraftInteractionController(QWidget(), presenter, write, lambda command, ready, _message: ready(command()))


def test_complete_form_validates_without_losing_inputs_and_waits_for_save():
    dialog = TermEditDialog(plugin_ids=("Base.esm",))
    submitted = []
    dialog.save_requested.connect(submitted.append)
    dialog.translation.setText("星辰")
    dialog.save_button.click()
    assert "原名" in dialog.error.text()
    assert dialog.translation.text() == "星辰"
    dialog.original.setText("Star")
    dialog.scope.setCurrentIndex(dialog.scope.findData(ScopeKind.PLUGIN))
    dialog.save_button.click()
    assert "插件名称" in dialog.error.text()
    dialog.plugin.setCurrentText("Base.esm")
    dialog.variants.setPlainText("Stars\nStars")
    dialog.save_button.click()
    assert "重复" in dialog.error.text()
    assert not submitted
    dialog.variants.setPlainText("Stars\nThe Star")
    dialog.notes.setPlainText("此处为专名\n保留第二行")
    dialog.save_button.click()
    assert len(submitted) == 1
    assert submitted[0].scope == TermScope.plugin("Base.esm")
    assert submitted[0].variants == ("Stars", "The Star")
    assert not dialog.save_button.isEnabled()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.save_failed(ValueError("名称已存在"))
    assert dialog.original.text() == "Star"
    assert dialog.notes.toPlainText() == "此处为专名\n保留第二行"
    assert "名称已存在" in dialog.error.text()
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    dialog.saved()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_add_prepares_before_asking_for_input_and_guides_first_time_user(monkeypatch):
    pending, prompts, build_navigation = [], [], []
    presenter = SimpleNamespace(term_editing=SimpleNamespace(prepare_draft=lambda: None))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: prompts.append(args[2]))
    monkeypatch.setattr(TermEditDialog, "exec", lambda _dialog: pytest.fail("No form before a draft exists"))
    controller = DraftInteractionController(
        QWidget(),
        presenter,
        lambda command: pytest.fail("No write without a draft"),
        lambda command, ready, _message: pending.append((command, ready)),
        on_build_required=lambda: build_navigation.append(True),
    )
    controller.add()
    assert len(pending) == 1 and not prompts
    command, ready = pending.pop()
    ready(command())
    assert "从已登记的来源生成草稿" in prompts[0]
    assert build_navigation == [True]


def test_real_edit_after_publication_keeps_effective_terms_and_saves_all_fields(harness, monkeypatch):
    harness.build()
    published = harness.publish()
    original = published.decisions[0]
    controller = _controller(harness)

    def edit(dialog):
        assert dialog.original.text() == "Dragon"
        dialog.original.setText("Ancient Dragon")
        dialog.translation.setText("远古巨龙")
        dialog.scope.setCurrentIndex(dialog.scope.findData(ScopeKind.PLUGIN))
        dialog.plugin.setCurrentText("Dragon.esp")
        dialog.variants.setPlainText("Ancient Dragons\nElder Dragon")
        dialog.notes.setPlainText("仅用于该插件中的生物名称")
        dialog.save_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted, dialog.error.text()
        return dialog.result()

    monkeypatch.setattr(TermEditDialog, "exec", edit)
    controller.edit_effective(original)
    draft = harness.commands.active_draft(harness.context)
    edited = next(item for item in draft.decisions if not item.suppressed)
    assert edited.original == "Ancient Dragon"
    assert edited.translation == "远古巨龙"
    assert edited.scope == TermScope.plugin("Dragon.esp")
    assert edited.variants == ("Ancient Dragons", "Elder Dragon")
    assert edited.notes == "仅用于该插件中的生物名称"
    assert edited.status is DecisionStatus.MANUAL_CONFIRMED
    assert harness.repository.effective_version("project-1", "main") == published


def test_duplicate_add_keeps_form_open_and_allows_correcting_the_name(harness, monkeypatch):
    harness.build()
    controller = _controller(harness)

    def add(dialog):
        dialog.original.setText("Dragon")
        dialog.translation.setText("新龙")
        dialog.variants.setPlainText("Dragonkin")
        dialog.notes.setPlainText("不要丢失此备注")
        dialog.save_button.click()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.save_button.isEnabled()
        assert dialog.notes.toPlainText() == "不要丢失此备注"
        dialog.original.setText("New Dragon")
        dialog.save_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted, dialog.error.text()
        return dialog.result()

    monkeypatch.setattr(TermEditDialog, "exec", add)
    controller.add()
    draft = harness.commands.active_draft(harness.context)
    added = next(item for item in draft.decisions if item.original == "New Dragon")
    assert added.variants == ("Dragonkin",)
    assert added.notes == "不要丢失此备注"


@pytest.mark.parametrize("from_effective", (False, True))
def test_draft_edits_exact_selected_term_while_effective_edits_follow_its_replacement(
    harness, monkeypatch, from_effective
):
    draft = harness.build()
    original = draft.decisions[0]
    updated = harness.commands.edit_term(
        original.term_id,
        harness.context,
        original="Ancient Dragon",
        translation="远古巨龙",
        scope=original.scope,
        variants=(),
        notes="替代词原有备注",
        expected_ref=draft.ref,
    )
    old = next(item for item in updated.decisions if item.term_id == original.term_id)
    replacement = next(item for item in updated.decisions if item.replacement_of == old.term_id)
    assert old.suppressed and not replacement.suppressed
    selected_target = replacement if from_effective else old

    def edit(dialog):
        assert dialog.original.text() == selected_target.original
        assert dialog.translation.text() == selected_target.translation
        dialog.notes.setPlainText("仅修改用户选择的词条")
        dialog.save_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted, dialog.error.text()
        return dialog.result()

    monkeypatch.setattr(TermEditDialog, "exec", edit)
    controller = _controller(harness)
    (controller.edit_effective if from_effective else controller.edit)(old)
    current = {item.term_id: item for item in harness.commands.active_draft(harness.context).decisions}
    assert current[selected_target.term_id].notes == "仅修改用户选择的词条"
    untouched = old if from_effective else replacement
    assert current[untouched.term_id] == untouched
    assert current[old.term_id].suppressed


def test_same_translation_save_confirms_real_review_required_term(harness, monkeypatch):
    draft = harness.build()
    review_term = replace(draft.decisions[0], status=DecisionStatus.REVIEW_REQUIRED)
    # Published manual text changing in its source requires a fresh human confirmation.
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=review_term.term_id, translation="巨龙"
    )
    harness.publish()
    harness.parser.entries["source-a"] = (_entry("source-a", "one", "Dragon", "飞龙"),)
    harness.change_source()
    draft = harness.build()
    review_term = draft.decisions[0]
    assert review_term.status is DecisionStatus.REVIEW_REQUIRED

    def confirm(dialog):
        dialog.save_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted, dialog.error.text()
        return dialog.result()

    monkeypatch.setattr(TermEditDialog, "exec", confirm)
    _controller(harness).edit(review_term)
    current = harness.commands.active_draft(harness.context).decisions[0]
    assert current.translation == review_term.translation
    assert current.status is DecisionStatus.MANUAL_CONFIRMED


def test_batch_applies_to_explicit_selection_and_asks_about_impact(harness, monkeypatch):
    harness.parser.entries["source-a"] += (_entry("source-a", "two", "Sword", "剑"),)
    draft = harness.build()
    controller = _controller(harness)
    questions = []

    def answer(_parent, _title, text, *_buttons):
        questions.append(text)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    controller.batch(draft.decisions, "confirm")
    current = harness.commands.active_draft(harness.context)
    assert all(item.status is DecisionStatus.MANUAL_CONFIRMED for item in current.decisions)
    assert "2 条术语" in questions[0] and "发布后生效" in questions[0]
    controller.batch(current.decisions[:1], "suppress")
    current = harness.commands.active_draft(harness.context)
    assert sum(item.suppressed for item in current.decisions) == 1
    controller.batch(tuple(item for item in current.decisions if item.suppressed), "reenable")
    assert not any(item.suppressed for item in harness.commands.active_draft(harness.context).decisions)


def test_batch_clears_previous_edit_focus_before_notifying_prepared(harness, monkeypatch):
    draft = harness.build()
    controller = _controller(harness)
    controller.focus_original = "Previously edited term"
    prepared_focus = []
    controller._on_prepared = lambda _draft: prepared_focus.append(controller.focus_original)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)

    controller.batch(draft.decisions, "confirm")

    assert prepared_focus == [None]
    assert controller.focus_original is None
    assert harness.commands.active_draft(harness.context) == draft


def test_batch_does_not_automatically_decide_conflicting_translations(monkeypatch):
    term = TermDecision("term", "p", "v", "Dragon", "dragon", "龙", status=DecisionStatus.UNRESOLVED)
    draft = SimpleNamespace(ref="revision", decisions=(term,))
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("Unresolved terms must be reviewed first"))
    controller = DraftInteractionController(
        QWidget(),
        SimpleNamespace(term_editing=SimpleNamespace(prepare_draft=lambda: draft)),
        lambda command: pytest.fail("Unresolved terms must not be written"),
        lambda command, ready, _message: ready(command()),
    )
    controller.batch(draft.decisions, "confirm")
    assert "异译审核" in messages[0]


def test_dirty_form_cannot_close_without_explicit_discard_and_cannot_close_during_save(monkeypatch):
    dialog = TermEditDialog()
    rejected = []
    dialog.rejected.connect(lambda: rejected.append(True))
    dialog.original.setText("Dragon")
    dialog.translation.setText("巨龙")
    dialog.show()
    assert dialog.windowModality() is Qt.WindowModality.WindowModal
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    dialog.close()
    assert not rejected
    assert dialog.isVisible()
    assert dialog.original.text() == "Dragon"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    dialog.set_saving(True)
    dialog.close()
    assert not rejected
    assert dialog.isVisible()
    dialog.set_saving(False)
    dialog.close()
    assert rejected == [True]
    assert not dialog.isVisible()


def test_batch_rejects_selection_that_changed_since_the_user_saw_it(harness, monkeypatch):
    draft = harness.build()
    harness.commands.apply_decision(
        "change_translation", harness.context, term_id=draft.decisions[0].term_id, translation="新的译名"
    )
    before = harness.commands.active_draft(harness.context)
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: pytest.fail("Stale selection requires a fresh review"))
    _controller(harness).batch(draft.decisions, "confirm")
    assert "选择后发生了变化" in messages[0]
    assert harness.commands.active_draft(harness.context) == before


def test_draft_multi_selection_has_count_and_does_not_offer_bulk_translation_choices():
    model = QStandardItemModel()
    terms = tuple(TermDecision(str(index), "p", "v", f"Term {index}", f"term {index}", "词") for index in range(3))
    for term in terms:
        item = QStandardItem(term.original)
        item.setData(term, Qt.ItemDataRole.UserRole)
        model.appendRow(item)
    view = DraftView(model)
    selected = []
    view.batch_requested.connect(lambda items, action: selected.append((items, action)))
    for index in (0, 2):
        view.table.selectionModel().select(model.index(index, 0), QItemSelectionModel.SelectionFlag.Select)
    assert view.selection_label.text() == "本页已选 2 条"
    assert not view.edit_button.isEnabled()
    view.confirm_button.click()
    assert selected == [((terms[0], terms[2]), "confirm")]
    unresolved = replace(terms[0], status=DecisionStatus.UNRESOLVED)
    model.item(0).setData(unresolved, Qt.ItemDataRole.UserRole)
    assert not view.confirm_button.isEnabled()
    assert not view.reenable_button.isEnabled()
    assert view.suppress_button.isEnabled()
    view.close()
