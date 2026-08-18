from __future__ import annotations

import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.application.projections import ProjectionSnapshot, ProjectionStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.workbench.step2 import Step2PreviewWidget


class _Config:
    token = ""


_APP: QApplication | None = None


def _application() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _entries(count: int) -> TranslationEntryCollection:
    return TranslationEntryCollection(
        TranslationEntry(str(index), f"key-{index:04d}", f"Original {index}", "", 0, "NPC_:FULL")
        for index in range(count)
    )


def _drain_render(app: QApplication, widget: Step2PreviewWidget, expected: int) -> None:
    deadline = time.monotonic() + 5
    while widget._table.rowCount() < expected and time.monotonic() < deadline:
        app.processEvents()


def test_large_collection_automatically_renders_every_batch(monkeypatch) -> None:
    app = _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.resize(1_200, 800)
    widget.show()

    widget.refresh(_entries(1_200))
    app.processEvents()

    assert 0 < widget._table.rowCount() < 1_200
    assert widget.get_filtered_count() == 1_200

    _drain_render(app, widget, 1_200)

    assert widget._table.rowCount() == 1_200
    assert widget.get_filtered_count() == 1_200


def test_locate_entry_selects_when_automatic_render_reaches_it(monkeypatch) -> None:
    app = _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.refresh(_entries(1_200))

    widget.locate_entry("1199")
    _drain_render(app, widget, 1_200)

    assert widget._table.rowCount() == 1_200
    assert widget._table.currentRow() == 1_199


class _RebuildingCommands:
    def __init__(self) -> None:
        self.context: context_module.AppContext | None = None

    def update_entry(self, _key, _runtime_context, **_changes):
        assert self.context is not None
        # Reproduce a synchronous subscriber refresh that deletes the edited
        # QTableWidgetItem before update_projected_entry returns.
        self.context.label_data_changed.emit()
        return SimpleNamespace(is_success=True)


class _ProjectionUpdatingCommands:
    def __init__(self, store: ProjectionStore) -> None:
        self.store = store

    def update_entry(self, _key, _runtime_context, **_changes):
        current = self.store.snapshot()
        assert current is not None
        self.store.rebuild(
            ProjectionSnapshot(
                current.stream_id,
                current.revision + 1,
                current.persisted_revision,
                current.to_dict()["values"],
            )
        )
        return SimpleNamespace(is_success=True)


def test_translation_edit_survives_synchronous_table_rebuild(monkeypatch) -> None:
    app = _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    store = ProjectionStore(
        ProjectionSnapshot(
            "project:project-a",
            1,
            1,
            {"variant_id": "variant-a", "label_library": {}, "entries": []},
        )
    )
    commands = _RebuildingCommands()
    context = context_module.AppContext(
        project_projection=store,
        project_commands=commands,
        runtime_context=object(),
    )
    commands.context = context
    collection = _entries(1)
    widget = Step2PreviewWidget(context)
    widget.refresh(collection)
    app.processEvents()

    edited_item = widget._table.item(0, 3)
    edited_item.setText("Translated")
    app.processEvents()

    assert collection.get_by_id("0").translation == "Translated"
    assert widget._table.item(0, 3).text() == "Translated"
    context.close_projection()


def test_translation_projection_update_does_not_restart_full_render(monkeypatch) -> None:
    app = _application()
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    store = ProjectionStore(
        ProjectionSnapshot(
            "project:project-a",
            1,
            1,
            {"variant_id": "variant-a", "label_library": {}, "entries": []},
        )
    )
    context = context_module.AppContext(
        project_projection=store,
        project_commands=_ProjectionUpdatingCommands(store),
        runtime_context=object(),
    )
    collection = _entries(1)
    widget = Step2PreviewWidget(context)
    widget.refresh(collection)
    app.processEvents()
    generation = widget._render_generation

    widget._table.item(0, 3).setText("Translated")
    app.processEvents()

    assert widget._render_generation == generation
    assert collection.get_by_id("0").translation == "Translated"
    assert widget._table.item(0, 3).text() == "Translated"
    context.close_projection()
