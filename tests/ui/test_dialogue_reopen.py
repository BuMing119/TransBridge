"""Applying a real Variant edit publishes reusable synchronization evidence."""

from PyQt6.QtWidgets import QWidget
import pytest

from tests.ui import test_dialogue_authority as authority_tests
from tests.ui.test_dialogue_editor import drain
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.workbench.step2 import Step2PreviewWidget

authority = authority_tests.authority


@pytest.fixture
def editor(authority):
    context, store, _, _, _, _ = authority
    parent = QWidget()
    preview = Step2PreviewWidget(context, parent)
    preview.refresh(context.collection)
    controller = DialogueEditorController(context, parent, preview, [], projection=store)
    drain(controller)
    yield controller
    drain(controller)
    controller.close()
    preview.close()
    parent.close()
    parent.deleteLater()


def test_apply_close_reopen_reuses_committed_projection(editor, authority, monkeypatch):
    context, _, _, _, target, _ = authority
    editor.open_entry(target.identity)
    editor.view.translation.setPlainText("已同步的译文")
    editor.apply()
    assert not editor.dialog.isVisible()

    def unnecessary_projection(*_args):
        pytest.fail("Already-published data was projected again on reopen")

    monkeypatch.setattr("transbridge.ui.dialogue.projection_sync.apply_variant_projection", unnecessary_projection)
    editor.open_entry(target.identity)
    assert editor.dialog.isVisible()
    assert editor.view.translation.toPlainText() == "已同步的译文"
    assert context.collection.get(target.identity).translation == "已同步的译文"


def test_external_authoritative_edit_after_apply_is_visible_on_reopen(editor, authority):
    context, _, _, _, target, _ = authority
    editor.open_entry(target.identity)
    editor.view.translation.setPlainText("本地提交")
    editor.apply()
    assert context.project_commands.replace_entry_states(
        {target.identity: ("外部新译文", 3)}, context.runtime_context
    ).is_success
    editor.open_entry(target.identity)
    assert editor.view.translation.toPlainText() == "外部新译文"
    assert context.collection.get(target.identity).stage == 3


def test_batch_sync_also_preserves_latest_projection_for_reopen(editor, authority, monkeypatch):
    context, _, _, _, target, _ = authority
    matching = [entry for entry in context.collection if entry.original == target.original]
    assert len(matching) > 1
    monkeypatch.setattr("transbridge.ui.dialogue.sync_dialog.SyncTranslationDialog.exec", lambda self: 1)
    editor.open_entry(target.identity)
    editor.view.translation.setPlainText("同原文统一结果")
    editor.apply()
    assert not editor.dialog.isVisible()
    monkeypatch.setattr(
        "transbridge.ui.dialogue.projection_sync.apply_variant_projection",
        lambda *_: pytest.fail("Batch publication was projected twice"),
    )
    editor.open_entry(matching[-1].identity)
    assert editor.view.translation.toPlainText() == "同原文统一结果"


def test_failed_commit_does_not_mark_new_external_state_as_already_applied(editor, authority):
    context, _, _, _, target, _ = authority
    editor.open_entry(target.identity)
    editor.view.translation.setPlainText("旧草稿")
    assert context.project_commands.replace_entry_states(
        {target.identity: ("外部先提交", 1)}, context.runtime_context
    ).is_success
    editor.apply()
    assert editor.dialog.isVisible()
    assert "工程中的词条已变化" in editor.view.message.text()
    editor.discard()
    assert editor.view.translation.toPlainText() == "外部先提交"
