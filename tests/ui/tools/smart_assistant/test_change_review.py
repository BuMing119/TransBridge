"""Preview/result controls remain honest, actionable and bound to their original view."""

from dataclasses import replace
from threading import Event
from types import SimpleNamespace

from PyQt6.QtWidgets import QWidget

from tests.smart_assistant.test_operation_change_preview import _context
from tests.ui.tools.smart_assistant.test_confirmation_lifecycle import _APP, _view
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.application.assistant_requests.result_review import RecordedChange, RecordedOutcome, RequestReview
from transbridge.application.contracts import RequestContext
from transbridge.smart_assistant.operation_preview import build_operation_previews
from transbridge.ui.tools.smart_assistant.change_preview_view import ChangeReviewView
from transbridge.ui.tools.smart_assistant.result_review import RequestResultReviewBinding, RequestReviewDialog
from transbridge.ui.tools.smart_assistant.tool_card import ToolCard


def _review(**kwargs):
    return RequestReview("r", "统一术语", (("project_id", "p"), ("variant_id", "v")), **kwargs)


def test_full_preview_is_readable_and_json_is_secondary():
    step = {"tool": "edit_translation", "args": {"entry_id": "a", "new_translation": "New text"}}
    card = ToolCard(step)
    card.set_change_previews(build_operation_previews([step], _context()))
    assert "old A" in card._preview._summary.text()
    assert "New text" in card._preview._summary.text()
    assert "高级" in card._preview._toggle.text() and card._preview._details.isHidden()
    card._preview._all.click()
    dialog = card._preview._dialog
    table = dialog.findChild(ChangeReviewView)
    assert table.model.rowCount() == 1
    assert "New text" in table.full_text.toPlainText()
    dialog.close()
    card.close()


def test_late_preview_never_reenables_expired_confirmation(monkeypatch):
    view, parent, intents, _ = _view()
    view._preview_loader.context_provider = _context
    entered, proceed = Event(), Event()
    original = build_operation_previews

    def delayed(*args):
        entered.set()
        assert proceed.wait(5)
        return original(*args)

    monkeypatch.setattr(
        "transbridge.ui.tools.smart_assistant.confirmation_preview_binding.build_operation_previews", delayed
    )
    card = view.add_tool_card({"tool": "edit_translation", "args": {"entry_id": "a", "new_translation": "changed"}})
    try:
        _until(entered.is_set)
        assert not card._exec_btn.isEnabled()
        view.invalidate_pending()
    finally:
        proceed.set()
    _APP.processEvents()
    assert not card._exec_btn.isEnabled() and "失效" in card._exec_btn.text() and not intents
    view.close()
    parent.close()


def test_result_dialog_has_only_evidenced_navigation_and_failure_targets():
    calls = []
    row = RecordedChange("a", None, "before", "after", 1, 1, "edit_translation")
    review = _review(changes=(row,), outcomes=(RecordedOutcome("set_stage", "failed", "not found", "b"),))
    dialog = RequestReviewDialog(review, locate=calls.append)
    assert not dialog.open_folder.isVisible() and dialog.undo.isHidden()
    dialog.locate.click()
    assert calls[-1] == ("a",)
    dialog.filter.setChecked(True)
    assert dialog.changes.isHidden()
    dialog.locate.click()
    assert calls[-1] == ("b",)
    assert "not found" in dialog.outcomes.toPlainText()
    dialog.close()


def test_old_review_read_and_old_dialog_actions_cannot_cross_context(monkeypatch):
    jobs, located = [], []
    parent = QWidget()
    context = RequestContext("owner", session_id="s", project_id="p", variant_id="v")
    binding = SimpleNamespace(
        context=context,
        _turn_generation=1,
        _closed=False,
        facade=parent,
        service=object(),
        background=SimpleNamespace(submit=lambda work, received, **kw: jobs.append((work, received))),
        undo=SimpleNamespace(request=lambda _: None),
    )
    review = _review(changes=(RecordedChange("a", None, "old", "new", 1, 1, "edit_translation"),))
    monkeypatch.setattr("transbridge.ui.tools.smart_assistant.result_review.read_request_review", lambda *_: review)
    adapter = RequestResultReviewBinding(binding, locate_entries=lambda *args: located.append(args))
    adapter.show("r")
    work, received = jobs.pop()
    binding.context = replace(context, session_id="other")
    received(work())
    assert adapter.dialog is None
    binding.context = context
    adapter.show("r")
    work, received = jobs.pop()
    received(work())
    dialog = adapter.dialog
    binding._turn_generation += 1
    dialog.locate.click()
    assert not located
    adapter.close()
    parent.close()
