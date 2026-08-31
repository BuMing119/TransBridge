"""Exercise real Variant commands and projection refresh, including optimistic conflicts."""

from dataclasses import replace
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.dialogue_support import dialogue_entries
from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence.v2 import (
    ProjectDto,
    ProjectRef,
    SchemaEnvelope,
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantSnapshot,
)
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.ui import context as context_module
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.dialogue.editing import EntryDraft
from transbridge.ui.shell.navigation_rail import WorkspaceShell
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def authority(monkeypatch):
    entries = dialogue_entries()
    target = entries[2]
    project_id = ProjectId("project")
    project_ref = ProjectRef(project_id)
    variant_ref = VariantRef(VariantId("variant"), project_id)
    other_key = EntryKey(SourceNamespace("other-plugin"), target.key)
    aggregate = VariantAggregate(
        VariantSnapshot(
            variant_ref,
            (SourceFingerprint(target.identity.namespace, "a" * 64), SourceFingerprint(other_key.namespace, "b" * 64)),
            tuple(VariantEntryState(e.identity, labels=("keep",)) for e in entries)
            + (VariantEntryState(other_key, translation="其他来源", stage=1),),
        )
    )

    def snapshot():
        variant = aggregate.snapshot()
        return ProjectionSnapshot(
            "project:project",
            variant.revision,
            0,
            {
                "project_id": "project",
                "variant_id": "variant",
                "project_revision": 0,
                "variant_revision": variant.revision,
                "entries": [e.to_dict() for e in variant.entries],
            },
        )

    store = ProjectionStore(snapshot())

    class Lifecycle:
        active = SimpleNamespace(
            project=ProjectDto(SchemaEnvelope(2, project_ref.kind, "project", 0, {})),
            variant=aggregate,
            formal_variant_ref=variant_ref,
        )
        before_publish = None

        def commit_active_variant(self, changes, runtime, *, expected_project_revision):
            assert expected_project_revision == 0
            revision = aggregate.commit(changes, runtime)
            if self.before_publish:
                self.before_publish()
            store.rebuild(snapshot())
            return OperationResult.completed({"revision": revision})

    lifecycle = Lifecycle()
    commands = GuiProjectCommandFacade(lifecycle, None, lambda: None)
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext(
        project_projection=store,
        project_commands=commands,
        runtime_context=RequestContext("ui", run_id="dialogue-test"),
    )
    context.add_slot(
        "fixture.esp",
        context_module.CollectionSlot(
            "Plugin",
            TranslationEntryCollection(entries),
            esp_path="fixture.esp",
            format_id="plugin.sse",
        ),
    )
    yield context, store, aggregate, lifecycle, target, other_key
    context.close_projection()


def test_commit_updates_real_variant_then_projection_preserving_identity_metadata_and_labels(authority):
    context, store, aggregate, lifecycle, target, other_key = authority
    before = context.collection.get(target.identity)
    draft = EntryDraft.capture(context, before)
    draft.text = "  新译文\n<Alias=Player>  "
    observations = []
    lifecycle.before_publish = lambda: observations.append(context.collection.get(target.identity).translation)
    assert draft.commit(context, projection=store) is None
    assert observations == [""]  # No UI mutation before the authority commits.
    states = {e.entry_key: e for e in aggregate.snapshot().entries}
    assert states[target.identity].translation == draft.text
    assert states[target.identity].stage.value == 1
    assert states[target.identity].labels == ("keep",)
    assert states[other_key].translation == "其他来源"
    visible = context.collection.get(target.identity)
    assert visible.translation == draft.text
    assert visible.revision == states[target.identity].revision
    assert visible.metadata == before.metadata
    assert visible.original == before.original
    assert context.dirty
    persisted = aggregate.snapshot().to_dto().envelope.data
    assert any(e["translation"] == draft.text for e in persisted["entries"])


def test_stale_project_revision_fails_without_projection_mutation(authority, monkeypatch):
    context, store, aggregate, _, target, _ = authority
    draft = EntryDraft.capture(context, context.collection.get(target.identity))
    draft.text = "未提交"
    monkeypatch.setattr(context, "_project_revision", -1)
    assert draft.commit(context, projection=store)
    assert draft.text == "未提交"
    assert context.collection.get(target.identity).translation == ""
    assert aggregate.snapshot().revision == 0


def test_new_authoritative_translation_cannot_be_overwritten_through_stale_ui(authority):
    context, store, _, _, target, _ = authority
    draft = EntryDraft.capture(context, context.collection.get(target.identity))
    draft.text = "旧草稿"
    assert context.project_commands.replace_entry_states(
        {target.identity: ("其他操作的新译文", 1)},
        context.runtime_context,
    ).is_success
    # Existing UI projection intentionally has not refreshed yet.
    assert context.collection.get(target.identity).translation == ""
    assert "工程中的词条已变化" in draft.commit(context, projection=store)
    assert draft.text == "旧草稿"


def test_version_change_rejects_draft_even_with_same_entry_keys(authority, monkeypatch):
    context, store, aggregate, _, target, _ = authority
    draft = EntryDraft.capture(context, context.collection.get(target.identity))
    draft.text = "wrong variant"
    monkeypatch.setattr(context, "_active_variant_id", "different")
    assert "切换" in draft.commit(context, projection=store)
    assert aggregate.snapshot().revision == 0


def test_missing_service_does_not_fall_back_to_legacy_mutation(authority, monkeypatch):
    context, store, aggregate, _, target, _ = authority
    draft = EntryDraft.capture(context, context.collection.get(target.identity))
    draft.text = "no service"
    monkeypatch.setattr(context, "_project_commands", None)
    assert "不可用" in draft.commit(context, projection=store)
    assert aggregate.snapshot().revision == 0


@pytest.mark.parametrize("source_mode", ["dialogue", "ordinary", "eet"])
def test_main_table_and_dialogue_editor_share_the_authoritative_projection(authority, source_mode):
    context, store, aggregate, _, target, _ = authority
    if source_mode == "eet":
        context.active_slot.format_id = "xml.eet"
    elif source_mode == "ordinary":
        context.collection = TranslationEntryCollection(replace(e, context="MGEF:FULL") for e in context.collection)
    shell = WorkspaceShell()
    preview = Step2PreviewWidget(context)
    preview.refresh(context.collection)
    shell.addTab(preview, "工作台")
    shell.addTab(QWidget(), "ParaTranz")
    shell.addTab(QWidget(), "开始")
    editor = DialogueEditorController(context, shell, preview, [], projection=store)

    def drain():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            _APP.processEvents()
            if not editor._workers:
                return
            QTest.qWait(1)
        pytest.fail("index worker did not finish")

    try:
        drain()
        item = preview._table.item(2, COL_TRANSLATION)
        assert item.data(Qt.ItemDataRole.UserRole) is context.collection.get(target.identity)
        item.setText("主表输入")
        assert context.collection.get(target.identity).translation == "主表输入"
        editor.open_entry(target.identity)
        drain()
        assert editor.dialog.isVisible() and editor.dialog.isWindow()
        assert editor.view.context_panel.isEnabled() == (source_mode == "dialogue")
        assert editor.view.translation.toPlainText() == "主表输入"
        editor.view.translation.setPlainText("关联编辑输入")
        editor.apply()
        drain()
        stored = next(e for e in aggregate.snapshot().entries if e.entry_key == target.identity)
        assert stored.translation == "关联编辑输入"
        assert preview._table.item(2, COL_TRANSLATION).text() == "关联编辑输入"
        assert context.collection.get(target.identity).revision == stored.revision
        editor.dialog.close()
        editor.open_entry(target.identity)
        assert editor.view.translation.toPlainText() == "关联编辑输入"
    finally:
        drain()
        editor.close()
        preview.close()
        shell.close()
        shell.deleteLater()
        _APP.processEvents()
