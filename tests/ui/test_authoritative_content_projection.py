"""Persisted authority updates must reach the visible workbench without reopening it."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPersistentModelIndex
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry import STAGE_TRANSLATED, TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.context import AppContext, CollectionSlot
from transbridge.ui.workbench.stats_panel import CollectionStatsPanel
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.translation_table_columns import COL_KEY, COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


def _entries(source, count):
    return tuple(
        TranslationEntry(
            f"entry-{index:03}",
            f"key-{index:03}",
            f"Original {index:03}",
            "",
            0,
            "NPC_:FULL",
            entry_key=EntryKey(SourceNamespace(source), f"key-{index:03}"),
        )
        for index in range(count)
    )


def _snapshot(entries, revision=0):
    return ProjectionSnapshot(
        "project:projection-regression",
        revision,
        revision,
        {
            "project_id": "projection-regression",
            "variant_id": "default",
            "variant_revision": revision,
            "entries": [
                {
                    "entry_key": entry.identity.to_dict(),
                    "translation": entry.translation,
                    "stage": entry.stage,
                    "external_refs": [reference.to_dict() for reference in entry.external_refs],
                    "revision": entry.revision.value,
                }
                for entry in entries
            ],
        },
    )


def _translated(entries, text="云端译文", revision=1):
    return tuple(
        replace(entry, translation=f"{text} {entry.key}", stage=STAGE_TRANSLATED, revision=EntryRevision(revision))
        for entry in entries
    )


@pytest.fixture
def workbench(monkeypatch):
    monkeypatch.setattr("transbridge.ui.context.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token=""))
    first, second = _entries("source:first", 44), _entries("source:second", 2)
    store = ProjectionStore(_snapshot(first + second))
    context = AppContext(project_projection=store)
    preview = Step2PreviewWidget(context)
    stats = CollectionStatsPanel(context)
    context.add_slot("first", CollectionSlot("First.esp", TranslationEntryCollection(first)))
    context.add_slot("second", CollectionSlot("Second.esp", TranslationEntryCollection(second)))
    context.activate_slot("first")
    preview.resize(1200, 650)
    preview.show()
    _APP.processEvents()
    assert not context.authoritative_projection_diverged()
    yield SimpleNamespace(context=context, store=store, preview=preview, stats=stats, first=first, second=second)
    preview.close()
    stats.close()
    context.close_projection()
    preview.deleteLater()
    stats.deleteLater()
    _APP.processEvents()


def _publish(workbench, entries, *, background=False, revision=1):
    snapshot = _snapshot(entries, revision)
    if background:
        with ThreadPoolExecutor(max_workers=1) as pool:
            decision = pool.submit(workbench.store.rebuild, snapshot).result(timeout=5)
    else:
        decision = workbench.store.rebuild(snapshot)
    assert decision.applied
    assert not decision.diagnostics
    _APP.processEvents()


def _displayed(preview):
    table = preview._table
    return tuple(
        (table.entry_at_row(row).identity, table.model().index(row, COL_TRANSLATION).data())
        for row in range(table.rowCount())
    )


@pytest.mark.parametrize("background", [False, True])
def test_persisted_translations_refresh_table_and_both_statistics_immediately(workbench, background):
    view, context = workbench.preview, workbench.context
    observations = []
    context.collection_changed.connect(
        lambda _collection: observations.append(context.authoritative_projection_diverged())
    )
    translated = _translated(workbench.first)

    _publish(workbench, translated + workbench.second, background=background)

    assert _displayed(view) == tuple((entry.identity, entry.translation) for entry in translated)
    assert observations == [False]
    assert not context.authoritative_projection_diverged()
    assert view._summary.untranslated == 0
    assert view._summary.completed == 44
    assert view._summary_view._buttons["untranslated"].toolTip() == "翻译状态：未翻译，共 0 条；点击筛选"
    assert workbench.stats._lbl_translated.text() == "已翻译：44（100%）"
    assert workbench.stats._tree.topLevelItem(0).text(2) == "44"
    assert workbench.store.snapshot().dirty is False


def test_inactive_content_updates_without_resetting_visible_table_and_is_ready_on_switch(workbench):
    context, view = workbench.context, workbench.preview
    context.activate_slot("second")
    _APP.processEvents()
    visible = context.collection
    generation = view._table.render_session.generation
    observations = []
    context.collection_changed.connect(lambda _collection: observations.append(True))
    translated = _translated(workbench.first)

    _publish(workbench, translated + workbench.second, background=True)

    assert context.collection is visible
    assert observations == []
    assert view._table.render_session.generation == generation
    assert _displayed(view) == tuple((entry.identity, "（无译文）") for entry in workbench.second)
    assert workbench.stats._lbl_translated.text() == "已翻译：0（0%）"
    assert not context.authoritative_projection_diverged()

    context.activate_slot("first")
    _APP.processEvents()

    assert _displayed(view) == tuple((entry.identity, entry.translation) for entry in translated)
    assert view._summary.completed == 44
    assert workbench.stats._lbl_translated.text() == "已翻译：44（100%）"


def test_same_local_keys_in_other_source_receive_only_their_own_authoritative_content(workbench):
    first = _translated(workbench.first, "第一个插件")
    second = _translated(workbench.second, "另一个插件")

    _publish(workbench, first + second)

    assert _displayed(workbench.preview) == tuple((entry.identity, entry.translation) for entry in first)
    workbench.context.activate_slot("second")
    _APP.processEvents()
    assert _displayed(workbench.preview) == tuple((entry.identity, entry.translation) for entry in second)
    assert workbench.preview._summary.total == 2
    assert workbench.preview._summary.completed == 2
    assert workbench.stats._lbl_translated.text() == "已翻译：2（100%）"
    assert not workbench.context.authoritative_projection_diverged()


def test_small_authoritative_refresh_preserves_filter_sort_selection_and_scroll(workbench):
    view, table = workbench.preview, workbench.preview._table
    view.apply_filter_state({"search_key": "key-"})
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    table.horizontalHeader().sectionClicked.emit(COL_KEY)
    table.selectRow(30)
    table.verticalScrollBar().setValue(22)
    _APP.processEvents()
    selected = view.selected_row_entry_ids()
    filters = view._filters_view.state()
    order = tuple(table.entry_at_row(row).identity for row in range(table.rowCount()))
    scroll = table.verticalScrollBar().value()
    generation = table.render_session.generation
    persistent = QPersistentModelIndex(table.model().index(30, COL_TRANSLATION))
    translated = _translated(workbench.first)

    _publish(workbench, translated + workbench.second)

    expected = {entry.identity: entry.translation for entry in translated}
    assert dict(_displayed(view)) == expected
    assert view._filters_view.state() == filters
    assert view.selected_row_entry_ids() == selected
    assert tuple(table.entry_at_row(row).identity for row in range(table.rowCount())) == order
    assert table.verticalScrollBar().value() == scroll
    assert table.render_session.generation == generation
    assert persistent.isValid()
    assert persistent.data() == expected[table.entry_at_row(30).identity]


def test_untranslated_filter_recalculates_membership_from_new_authoritative_stages(workbench):
    view = workbench.preview
    view.apply_filter_state({"stage": [0], "search_key": "key-"})
    assert view._table.rowCount() == 44
    translated = _translated(workbench.first)

    _publish(workbench, translated + workbench.second)

    assert view._table.rowCount() == 0
    assert view._filters_view.state().stages == frozenset({0})
    assert view._summary.total == 44
    assert view._summary.untranslated == 0
    assert view._summary.completed == 44
    assert workbench.stats._lbl_translated.text() == "已翻译：44（100%）"
    view.apply_filter_state({"stage": [STAGE_TRANSLATED], "search_key": "key-"})
    assert _displayed(view) == tuple((entry.identity, entry.translation) for entry in translated)


def test_authoritative_refresh_does_not_hide_a_genuine_uncommitted_local_edit(workbench):
    context = workbench.context
    local = replace(workbench.first[0], translation="尚未提交的本地编辑", stage=3)
    context.collection = TranslationEntryCollection((local,) + workbench.first[1:])
    translated = _translated(workbench.first)

    _publish(workbench, translated + workbench.second)

    assert context.collection.get(local.identity).translation == "尚未提交的本地编辑"
    assert context.collection.get(local.identity).stage == 3
    assert context.collection.get(translated[1].identity).translation == translated[1].translation
    assert context.authoritative_projection_diverged()
    assert dict(_displayed(workbench.preview))[local.identity] == "尚未提交的本地编辑"
