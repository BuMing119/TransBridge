from dataclasses import replace
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.base import filter_entries
from transbridge.ui.context import AppContext, CollectionSlot
from transbridge.ui.entry_projection import EntryProjectionUpdate
from transbridge.ui.project_labels import entry_label_key, exact_label_key, project_entry_labels
from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter
from transbridge.ui.workbench.entry_action_scope import resolve_entry_action_scope
from transbridge.ui.workbench.filters_presenter import FiltersPresenter, FilterState
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_table import TranslationTable
from transbridge.ui.workbench.translation_table_columns import COL_TRANSLATION

_APP = QApplication.instance() or QApplication([])


def _entries():
    return tuple(
        TranslationEntry("same", "same", original, "", 0, "QUST:CNAM", requires_original_match=True)
        for original in ("first", "second")
    )


def _projection(entries):
    states = [
        {
            "entry_key": entry.identity.to_dict(),
            "translation": f"translation-{index}",
            "stage": 1,
            "labels": [f"label-{index}"],
        }
        for index, entry in enumerate(entries)
    ]
    return ProjectionSnapshot("test", 1, 0, {"entries": states})


def test_projection_updates_only_the_requested_original() -> None:
    first, second = _entries()
    snapshot = _projection((first, second))
    update = EntryProjectionUpdate.from_snapshot(snapshot, keys=(first.identity,))

    assert update.entry(first).translation == "translation-0"
    assert update.entry(second) is second
    complete = EntryProjectionUpdate(snapshot.values["entries"])
    assert [entry.translation for entry in complete.collection(TranslationEntryCollection((first, second)))] == [
        "translation-0",
        "translation-1",
    ]


def test_label_projection_retains_separate_memberships_and_reuses_them() -> None:
    first, second = _entries()
    states = _projection((first, second)).values["entries"]
    labels, exact = project_entry_labels(states, {})

    assert labels == {entry_label_key(first): {"label-0"}, entry_label_key(second): {"label-1"}}
    assert exact[exact_label_key(first.identity)] == {"label-0"}
    assert exact[exact_label_key(second.identity)] == {"label-1"}
    assert project_entry_labels(states, exact)[1] is exact
    context = SimpleNamespace(collection=(first, second), _project_projection=object(), _entry_labels_exact=exact)
    assert AppContext.entry_labels.fget(context) == labels


def test_label_filter_and_action_scope_do_not_include_same_key_sibling() -> None:
    first, second = _entries()
    presenter = FiltersPresenter()
    presenter.update(FilterState(labels=frozenset({"chosen"})))
    labels = {entry_label_key(first): {"chosen"}}

    assert presenter.apply((first, second), labels) == [first]
    assert resolve_entry_action_scope(first, (first, second), (entry_label_key(first),)) == (first,)
    assert resolve_entry_action_scope(second, (first, second), (entry_label_key(first),)) == (second,)
    assert filter_entries(TranslationEntryCollection((first, second)), {"labels": ["chosen"]}, labels) == [first]


def test_table_selection_and_row_refresh_distinguish_originals() -> None:
    first, second = _entries()
    table = TranslationTable(on_progress=lambda *_: None, on_batch=lambda: None)
    try:
        table.start_render(RenderSession(1, None, (first, second)), {}, {})
        for _ in range(10):
            _APP.processEvents()
            if not table.has_pending_batch:
                break
        assert table.find_entry_row(-1, entry_label_key(first)) == 0
        assert table.find_entry_row(-1, entry_label_key(second)) == 1
        table.selectRow(0)
        assert table.selected_entry_ids() == (entry_label_key(first),)
        table.update_rendered_entry(replace(first, translation="first-only", stage=1))
        assert table.model().index(0, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) == "first-only"
        assert table.model().index(1, COL_TRANSLATION).data(Qt.ItemDataRole.DisplayRole) != "first-only"
    finally:
        table.close_rendering()
        table.close()
        table.deleteLater()
        _APP.processEvents()


def test_ai_scope_resolves_exact_selected_original() -> None:
    first, second = _entries()
    workbench = SimpleNamespace(selected_entry_ids=lambda: (entry_label_key(first),))
    presenter = ScopePresenter(lambda: (first, second), lambda: {}, lambda _: "任务", workbench)
    presenter.select_preset("selection")
    assert presenter.candidates() == [first]


def test_authoritative_projection_divergence_checks_each_original(monkeypatch) -> None:
    first, second = _entries()
    snapshot = _projection((first, second))
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.create_or_load", lambda: SimpleNamespace(token="")
    )
    context = AppContext(project_projection=ProjectionStore(snapshot))
    try:
        context.add_slot(
            "source",
            CollectionSlot(
                "source",
                TranslationEntryCollection((
                    replace(first, translation="translation-0", stage=1),
                    replace(second, translation="translation-1", stage=1),
                )),
            ),
        )
        assert not context.authoritative_projection_diverged()
        context.collection = TranslationEntryCollection((
            replace(first, translation="translation-1", stage=1),
            replace(second, translation="translation-1", stage=1),
        ))
        assert context.authoritative_projection_diverged()
    finally:
        context.close_projection()
