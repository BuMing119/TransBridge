from __future__ import annotations

from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox
import pytest

from transbridge.application.contracts import OperationResult, RequestContext
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.application.projects.gui_facade import GuiProjectCommandFacade
from transbridge.converter.translation_entry import (
    STAGE_LABELS,
    STAGE_TRANSLATED,
    STAGE_UNTRANSLATED,
    TranslationEntry,
)
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
from transbridge.ui.workbench.filters_presenter import FilterState
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import COL_CHECK, COL_CONTEXT, COL_KEY, COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    collection = TranslationEntryCollection(
        TranslationEntry(
            str(index), f"key-{index}", f"Original {index}", f"译文 {index}", STAGE_TRANSLATED, "NPC_:FULL"
        )
        for index in range(3)
    )
    context.add_slot("source", context_module.CollectionSlot("Source", collection))
    context.activate_slot("source")
    widget = Step2PreviewWidget(context)
    widget.refresh(collection)
    confirmations = []

    def confirm(_parent, title, message, buttons, default):
        confirmations.append(message)
        assert title == "取消翻译"
        assert "清空译文" in message and "未翻译" in message
        assert default == QMessageBox.StandardButton.No
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    yield widget, tuple(collection), confirmations
    widget.close()
    widget.deleteLater()
    context.close_projection()
    _APP.processEvents()


def _cancel_action(widget, row):
    menu = widget._build_context_menu(row)
    return next(action for action in menu.actions() if action.text() == "取消翻译")


def test_cancel_translation_is_a_direct_action_and_resets_only_clicked_entry(preview, monkeypatch):
    widget, entries, confirmations = preview
    before = [entry.to_dict() for entry in entries]
    dirty = []
    monkeypatch.setattr(widget._ctx, "mark_dirty", lambda: dirty.append(True))

    action = _cancel_action(widget, 1)
    assert action.isEnabled()
    action.trigger()

    assert entries[1].translation == ""
    assert entries[1].stage == STAGE_UNTRANSLATED
    assert entries[1].original == before[1]["original"]
    assert entries[0].to_dict() == before[0]
    assert entries[2].to_dict() == before[2]
    assert widget._table.item(1, COL_TRANSLATION).text() == "（无译文）"
    assert "未翻译" in widget._table.item(1, COL_CONTEXT).text()
    assert widget._summary.untranslated == 1
    assert "1 条" in confirmations[0]
    assert dirty == [True]


@pytest.mark.parametrize("clicked_selected", [False, True])
def test_cancel_translation_uses_selected_ids_after_sorting(preview, clicked_selected):
    widget, entries, confirmations = preview
    table = widget._table
    table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Checked)
    table.item(2, COL_CHECK).setCheckState(Qt.CheckState.Checked)
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    assert table.item(0, COL_KEY).data(Qt.ItemDataRole.UserRole) is entries[2]

    _cancel_action(widget, 0 if clicked_selected else 1).trigger()

    affected = {0, 2} if clicked_selected else {1}
    for index, entry in enumerate(entries):
        assert entry.translation == ("" if index in affected else f"译文 {index}")
        assert entry.stage == (STAGE_UNTRANSLATED if index in affected else STAGE_TRANSLATED)
    assert f"{len(affected)} 条" in confirmations[0]


def test_declining_cancel_translation_keeps_everything_unchanged(preview, monkeypatch):
    widget, entries, _ = preview
    widget._table.selectAll()
    before = [entry.to_dict() for entry in entries]
    generation = widget._render_generation
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No)

    _cancel_action(widget, 0).trigger()

    assert [entry.to_dict() for entry in entries] == before
    assert widget._render_generation == generation


def test_right_click_on_selected_row_keeps_multi_selection(preview, monkeypatch):
    widget, entries, confirmations = preview
    widget.resize(1_200, 800)
    widget.show()
    _APP.processEvents()
    table = widget._table
    table.item(0, COL_CHECK).setCheckState(Qt.CheckState.Checked)
    table.item(2, COL_CHECK).setCheckState(Qt.CheckState.Checked)
    opened = []

    def execute(menu, _position):
        opened.append([action.text() for action in menu.actions()])
        next(action for action in menu.actions() if action.text() == "取消翻译").trigger()

    monkeypatch.setattr(QMenu, "exec", execute)
    position = table.visualItemRect(table.item(2, COL_KEY)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.RightButton, pos=position)
    # QTest does not synthesize the platform context-menu event on offscreen Qt.
    _APP.sendEvent(
        table.viewport(),
        QContextMenuEvent(QContextMenuEvent.Reason.Mouse, position, table.viewport().mapToGlobal(position)),
    )

    assert opened == [["标签", "翻译状态", "", "取消翻译"]]
    assert "2 条" in confirmations[0]
    assert entries[0].translation == entries[2].translation == ""
    assert entries[1].translation == "译文 1"


def test_cancel_translation_includes_selected_rows_still_loading_after_sort(preview):
    widget, _, confirmations = preview
    collection = TranslationEntryCollection(
        TranslationEntry(str(index), f"key-{index:04d}", "Original", "Translation", STAGE_TRANSLATED, "NPC_:FULL")
        for index in range(600)
    )
    widget._ctx.active_slot.collection = collection
    widget.refresh(collection)
    for _ in range(5):
        _APP.processEvents()
    table = widget._table
    assert table.rowCount() == 600
    table.selectAll()
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    assert table.rowCount() == 250

    _cancel_action(widget, 0).trigger()

    assert "600 条" in confirmations[0]
    assert all(entry.translation == "" and entry.stage == STAGE_UNTRANSLATED for entry in collection)
    assert widget._summary.untranslated == 600


@pytest.mark.parametrize("filter_kind", ["stage", "translation", "search_all"])
def test_cancel_translation_refreshes_filtered_membership_and_statistics(preview, filter_kind):
    widget, entries, _ = preview
    state = {
        "stage": FilterState(stages=frozenset({STAGE_TRANSLATED})),
        "translation": FilterState(search_translation="译文"),
        "search_all": FilterState(search_all="译文"),
    }[filter_kind]
    widget._filters_view.apply_state(state)
    widget._on_filters_changed()
    widget._table.selectAll()

    _cancel_action(widget, 0).trigger()

    assert all(entry.translation == "" and entry.stage == STAGE_UNTRANSLATED for entry in entries)
    assert widget._filters_view.state() == state
    assert widget.filtered_entries() == ()
    assert widget._table.rowCount() == 0
    assert widget._summary.untranslated == 3


def test_cancel_translation_disabled_when_scope_is_already_empty_and_untranslated(preview):
    widget, entries, confirmations = preview
    for entry in entries:
        entry.translation = ""
        entry.stage = STAGE_UNTRANSLATED
    widget._table.selectAll()

    action = _cancel_action(widget, 1)
    assert not action.isEnabled()
    action.trigger()
    assert not confirmations


@pytest.mark.parametrize("stage", sorted(STAGE_LABELS))
@pytest.mark.parametrize("translation", ["", "Existing translation"])
def test_cancel_translation_clears_draft_text_and_resets_all_existing_stages(preview, stage, translation):
    widget, entries, _ = preview
    target = replace(entries[0], stage=stage, translation=translation)
    widget.refresh(TranslationEntryCollection([target]))
    action = _cancel_action(widget, 0)

    assert action.isEnabled() == bool(translation or stage != STAGE_UNTRANSLATED)
    action.trigger()

    assert target.translation == ""
    assert target.stage == STAGE_UNTRANSLATED


def test_already_empty_entries_are_not_counted_as_changes(preview):
    widget, entries, confirmations = preview
    entries[1].translation = ""
    entries[1].stage = STAGE_UNTRANSLATED
    widget._table.selectAll()

    _cancel_action(widget, 0).trigger()

    assert "2 条" in confirmations[0]
    assert all(entry.translation == "" and entry.stage == STAGE_UNTRANSLATED for entry in entries)


@pytest.mark.parametrize("change", ["source", "collection", "translation"])
def test_content_changes_during_confirmation_abort_without_overwriting(preview, monkeypatch, change):
    widget, entries, _ = preview
    warnings = []
    before = entries[0].to_dict()

    def confirm(*_args):
        if change == "source":
            widget._ctx._active_key = "another-source"
        elif change == "collection":
            widget._ctx.active_slot.collection = TranslationEntryCollection([replace(entries[0])])
        else:
            entries[0].translation = "Newer translation"
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    _cancel_action(widget, 0).trigger()

    assert entries[0].translation == ("Newer translation" if change == "translation" else before["translation"])
    assert entries[0].stage == before["stage"]
    assert len(warnings) == 1
    assert "重新选择" in warnings[0]


@pytest.fixture
def authority(preview, monkeypatch):
    widget, visible, _ = preview
    context = widget._ctx
    project_id = ProjectId("project")
    project_ref = ProjectRef(project_id)
    variant_ref = VariantRef(VariantId("variant"), project_id)
    other_source = SourceNamespace("source:plugin:other")
    other_key = EntryKey(other_source, visible[0].key)
    aggregate = VariantAggregate(
        VariantSnapshot(
            variant_ref,
            (SourceFingerprint(visible[0].identity.namespace, "a" * 64), SourceFingerprint(other_source, "b" * 64)),
            tuple(
                VariantEntryState(
                    entry.identity,
                    translation=entry.translation,
                    stage=entry.stage,
                    labels=("keep",) if index == 0 else (),
                )
                for index, entry in enumerate(visible)
            )
            + (VariantEntryState(other_key, translation="Other source", stage=STAGE_TRANSLATED),),
        )
    )
    values = {
        "project_id": "project",
        "variant_id": "variant",
        "project_revision": 0,
        "variant_revision": 0,
        "label_library": {"keep": {"name": "Keep", "color": "#FFFFFF"}},
        "entries": [
            {"entry_key": entry.identity.to_dict(), "labels": ["keep"] if index == 0 else []}
            for index, entry in enumerate(visible)
        ],
    }
    store = ProjectionStore(ProjectionSnapshot("project:project", 0, 0, values))
    monkeypatch.setattr(context, "_project_projection", store)
    context._apply_project_projection(store.snapshot())
    before = [entry.to_dict() for entry in visible]

    class Lifecycle:
        active = SimpleNamespace(
            project=ProjectDto(SchemaEnvelope(2, project_ref.kind, "project", 0, {})),
            variant=aggregate,
            formal_variant_ref=variant_ref,
        )
        commits = []

        def commit_active_variant(self, changes, runtime, *, expected_project_revision):
            assert [entry.to_dict() for entry in visible] == before
            assert expected_project_revision == 0
            revision = aggregate.commit(changes, runtime)
            self.commits.append(revision)
            # A synchronous projection subscriber can rebuild every Qt item.
            context.label_data_changed.emit()
            return OperationResult.completed({"revision": revision})

    lifecycle = Lifecycle()
    monkeypatch.setattr(context, "_project_commands", GuiProjectCommandFacade(lifecycle, None, lambda: None))
    monkeypatch.setattr(context, "_runtime_context", RequestContext(owner_id="ui", run_id="reset"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    return aggregate, lifecycle, other_key, warnings


def test_authoritative_batch_commits_once_and_keeps_other_sources_and_labels(preview, authority):
    widget, entries, _ = preview
    aggregate, lifecycle, other_key, warnings = authority
    widget._reload_projected_labels()
    labels = widget.collect_labels()
    widget._table.selectAll()

    _cancel_action(widget, 0).trigger()

    assert lifecycle.commits == [1]
    states = {entry.entry_key: entry for entry in aggregate.snapshot().entries}
    assert states[other_key].translation == "Other source"
    assert states[other_key].stage.value == STAGE_TRANSLATED
    assert states[entries[0].identity].labels == ("keep",)
    for row, entry in enumerate(entries):
        assert entry.translation == states[entry.identity].translation == ""
        assert entry.stage == states[entry.identity].stage.value == STAGE_UNTRANSLATED
        assert widget._table.item(row, COL_TRANSLATION).text() == "（无译文）"
    assert widget.collect_labels() == labels
    assert not warnings


@pytest.mark.parametrize("failure", ["stale", "commit", "missing_service", "missing_identity"])
def test_authoritative_failure_keeps_visible_and_stored_translations(preview, authority, monkeypatch, failure):
    widget, entries, _ = preview
    aggregate, lifecycle, _, warnings = authority
    widget._table.selectAll()
    if failure == "missing_service":
        monkeypatch.setattr(widget._ctx, "_project_commands", None)
    elif failure == "missing_identity":
        monkeypatch.setattr(widget._ctx, "_active_project_id", None)
    elif failure == "stale":
        monkeypatch.setattr(widget._ctx, "_variant_revision", 99)
    else:
        monkeypatch.setattr(
            lifecycle,
            "commit_active_variant",
            lambda *_args, **_kwargs: OperationResult.failed(ValueError("Unable to save the changes")),
        )
    before = [entry.to_dict() for entry in entries]
    stored = aggregate.snapshot()

    _cancel_action(widget, 0).trigger()

    assert [entry.to_dict() for entry in entries] == before
    assert aggregate.snapshot() == stored
    assert not lifecycle.commits
    assert len(warnings) == 1
    assert "译文未改变" in warnings[0]
