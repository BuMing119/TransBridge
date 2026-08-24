from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.workbench.step2 import Step2PreviewWidget


class _Config:
    token = ""


_APP = QApplication.instance() or QApplication([])


def _entries() -> TranslationEntryCollection:
    return TranslationEntryCollection([
        TranslationEntry("a", "key-a", "Alpha", "", 0, "NPC_:FULL"),
        TranslationEntry("b", "key-b", "Beta", "译文", 1, "QUST:FULL"),
    ])


def test_filter_state_round_trip_preserves_visible_contract(monkeypatch) -> None:
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: _Config())
    widget = Step2PreviewWidget(context_module.AppContext())
    widget.refresh(_entries())
    _APP.processEvents()

    original = widget.get_filter_state()
    widget.apply_filter_state(original)
    _APP.processEvents()

    assert widget.get_filter_state() == original
    assert widget.get_filtered_count() == 2
    widget.close()
