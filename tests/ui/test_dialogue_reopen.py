"""Applying a real Variant edit publishes reusable synchronization evidence."""

from dataclasses import replace
from threading import get_ident

from PyQt6.QtWidgets import QWidget
import pytest

from tests.integration.gui import test_project_paged_workbench as paged_workbench
from tests.ui import test_dialogue_authority as authority_tests
from tests.ui.test_dialogue_editor import drain
from transbridge.application.projections import ProjectionSnapshot
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.variant import VariantChangeSet
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.workbench.step2 import Step2PreviewWidget

authority = authority_tests.authority
qapp = paged_workbench.qapp


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

    monkeypatch.setattr(ProjectionSnapshot, "to_dict", unnecessary_projection)
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
        ProjectionSnapshot,
        "to_dict",
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
    assert "草稿已保留" in editor.view.message.text()
    assert editor.view.translation.toPlainText() == "旧草稿"
    editor.discard()
    assert editor.view.translation.toPlainText() == "外部先提交"


def test_dialogue_apply_preserves_paged_collection_and_publishes_once(qapp, tmp_path, monkeypatch):
    root = tmp_path / "data"
    database, _variant = paged_workbench._create_batch_project(root, tmp_path)
    with paged_workbench._workbench(root, qapp) as (host, coordinator, services):
        failures = paged_workbench._begin_open(host, coordinator, database)
        paged_workbench._wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        context = host.context
        collection = context.collection
        assert isinstance(collection, ProjectRowsCollection)
        preview = host.workbench.preview
        paged_workbench._wait_until(qapp, lambda: preview._paging.resident_ready)
        previous_key, removed_key = (preview._table.entry_at_row(row).identity for row in (597, 598))
        key = preview._table.entry_at_row(599).identity
        assert context.project_commands.replace_entry_states({key: ("待清空", 1)}, context.runtime_context).is_success
        editor = DialogueEditorController(context, host.workbench, preview, [], projection=services.project_projection)
        scan_threads = []
        iter_entries = ProjectStore.iter_entries

        def observe_scan(*args, **kwargs):
            scan_threads.append(get_ident())
            yield from iter_entries(*args, **kwargs)

        monkeypatch.setattr(ProjectStore, "iter_entries", observe_scan)
        try:
            editor.open_entry(key)
            drain(editor)
            assert editor.view.translation.toPlainText() == "待清空"
            assert editor.view.browsing_mode == "record"
            assert not editor.view.context_reason.isVisible()
            assert not scan_threads

            def unexpected_scan(*_args, **_kw):
                pytest.fail("Applying one paged dialogue entry must not read the entire project")

            published = []
            context.collection_changed.connect(published.append)
            editor.view.translation.setPlainText("")
            with monkeypatch.context() as patch:
                patch.setattr(ProjectStore, "iter_entries", unexpected_scan)
                editor.apply()
            assert not editor.dialog.isVisible()
            assert len(published) == 1 and published[0] is collection
            assert context.collection is collection
            assert collection.get(key).translation == ""
            assert preview._table.entry_at_row(599).translation == ""

            editor.open_entry(key)
            editor.move(-1)
            assert editor._selected_key == removed_key
            editor.move(1)
            assert editor._selected_key == key

            lifecycle = services.project_lifecycle
            current = lifecycle.active.variant.snapshot()
            state = replace(current.entries.get(removed_key), tombstone=True)
            change = VariantChangeSet(
                current.ref,
                current.revision,
                current.source_fingerprints,
                current.entries.patched((state,)),
                current.label_library,
                context.runtime_context.run_id,
                sparse_entries=(state,),
            )
            assert lifecycle.commit_active_variant(
                change, context.runtime_context, expected_project_revision=context.project_revision
            ).is_success
            drain(editor)
            assert removed_key not in editor._navigation_keys
            editor.move(-1)
            assert editor._selected_key == previous_key
        finally:
            drain(editor)
            editor.close()
