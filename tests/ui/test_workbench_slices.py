from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication, QWidget

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.workbench.filters_presenter import FiltersPresenter, FilterState
from transbridge.ui.workbench.step1 import Step1SourceWidget
from transbridge.ui.workbench.step2 import Step2PreviewWidget
from transbridge.ui.workbench.widget import WorkbenchWidget


class _Config:
    token = ""


_APP = QApplication.instance() or QApplication([])


def _collection(count: int) -> TranslationEntryCollection:
    return TranslationEntryCollection(
        TranslationEntry(str(index), f"key-{index}", f"Original {index}", "", 0, "NPC_:FULL") for index in range(count)
    )


def test_filter_revision_is_independent_from_render_and_preserves_entry_identity() -> None:
    entries = list(_collection(3))
    presenter = FiltersPresenter()
    presenter.update(FilterState(search_key="key-1"))

    result = presenter.apply(entries, {})

    assert presenter.revision == 1
    assert result == [entries[1]]
    assert result[0] is entries[1]


def test_public_filtered_entries_is_not_limited_to_rendered_batch(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    widget = Step2PreviewWidget(context_module.AppContext())

    widget.refresh(_collection(600))

    assert widget._table.rowCount() == 250
    assert len(widget.filtered_entries()) == 600
    widget.close()


def test_step1_facade_composes_source_input_view(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    widget = Step1SourceWidget(context_module.AppContext())

    assert widget._parse_btn is widget._source_view.parse_button
    assert widget._esp_input is widget._source_view.esp_input
    assert widget._slot_combo is widget._source_view.slot_combo
    widget.close()


def _flush_deferred_deletes() -> None:
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    _APP.processEvents()


def test_destroy_mid_render_cancels_owned_batch_callback(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    widget = Step2PreviewWidget(context_module.AppContext())
    table = widget._table
    widget.refresh(_collection(1_200))

    assert table.rowCount() == 250
    assert table.has_pending_batch

    widget.deleteLater()
    _flush_deferred_deletes()
    _APP.processEvents()

    assert sip.isdeleted(widget)
    assert sip.isdeleted(table)


def test_step2_releases_batch_timer_for_100_lifecycles(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())

    for _ in range(100):
        widget = Step2PreviewWidget(context_module.AppContext())
        table = widget._table
        widget.refresh(_collection(600))
        assert table.has_pending_batch

        widget.close()
        assert not table.has_pending_batch
        widget.deleteLater()
        _flush_deferred_deletes()
        assert sip.isdeleted(widget)
        assert sip.isdeleted(table)


def test_ai_translation_progress_is_activated_after_config_window_closes() -> None:
    class ProgressWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def showNormal(self) -> None:
            self.calls.append("showNormal")

        def show(self) -> None:
            self.calls.append("show")

        def raise_(self) -> None:
            self.calls.append("raise")

        def activateWindow(self) -> None:
            self.calls.append("activate")

    host = type("Host", (), {"_tool_windows": {"ai_translator": object()}})()
    progress = ProgressWindow()

    WorkbenchWidget._on_progress_window_created(host, progress)

    assert host._tool_windows == {"ai_translator_progress": progress}
    assert progress.calls == []
    _APP.processEvents()
    assert progress.calls == ["show", "raise", "activate"]
