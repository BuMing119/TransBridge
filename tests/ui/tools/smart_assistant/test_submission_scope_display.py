"""The user's original selection remains visible after workbench changes and reloads."""

from dataclasses import replace

from PyQt6.QtWidgets import QPlainTextEdit

from tests.ui.tools.smart_assistant.test_conversation_layout import _request
from tests.ui.tools.smart_assistant.test_request_lifecycle_panel import _until
from transbridge.ui.tools.smart_assistant.submission_scope import saved_scopes, scope_from_input

pytest_plugins = ["tests.ui.tools.smart_assistant.test_request_lifecycle_panel"]


def test_legacy_scope_does_not_invent_current_selection():
    saved = scope_from_input({"context": {"project_id": "old-project"}, "selection": {}})
    assert "old-project" in saved.summary
    assert "选区未记录" in saved.summary
    assert "选中 0" not in saved.summary


def test_request_uses_original_scope_instead_of_later_followup():
    request = replace(_request(), source_message_ids=("second", "first"))
    scopes, _ = saved_scopes(
        {
            "ingress": [
                {"message_id": "first", "selection": {"selected_entry_ids": ["a"]}},
                {"message_id": "second", "selection": {"selected_entry_ids": ["b", "c"]}},
            ]
        },
        (request,),
    )
    assert "选中 1 条" in scopes["r"].summary
    assert "\na" in scopes["r"].details


def test_submitted_scope_survives_changed_workbench_and_session_reload(environment):
    chat, binding = environment.panel.chat, environment.binding
    chat._ctx.project_name = "Original project"
    chat._ctx.selected_ids = {"entry-a", "entry-b"}
    chat._ctx.active_version_identity = ("source", "version-1")
    chat.send_user_message("检查选中的词条")
    _until(lambda: environment.service.state(binding.context).get("requests"))
    _until(lambda: not binding._accepting and not binding.management._refresh._pending)
    chat._ctx.project_name = "Different project"
    chat._ctx.selected_ids = {"entry-z"}
    chat._ctx.active_version_identity = ("source", "version-2")
    chat._presentation.refresh()
    binding.refresh()
    _until(lambda: bool(chat._presentation.progress.cards) and not binding.management._refresh._pending)
    card = next(iter(chat._presentation.progress.cards.values()))
    assert "Original project" in card.scope_view.summary.text()
    assert "选中 2 条" in card.scope_view.summary.text()
    assert "version-1" in card.scope_view.summary.text()
    assert "Different project" in chat._presentation.context_label.full_text
    original = card.scope_view.summary.text()
    history = chat._conversation.get_transcript()
    chat._message_list.clear()
    chat.load_history(history)
    binding.refresh()
    _until(lambda: not binding.management._refresh._pending)
    assert card.scope_view.summary.text() == original
    users = [w for w in chat._message_list._owned_widgets if getattr(w, "role", None) == "user"]
    assert any(w._scope_view.summary.text() == original for w in users)
    card.scope_view.details_button.click()
    details = card.scope_view.findChild(QPlainTextEdit)
    assert details is not None and "entry-a" in details.toPlainText()
