"""Index work may finish after its owning window is destroyed."""

from threading import Event
from time import monotonic
from types import SimpleNamespace

from PyQt6 import sip
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.dialogue_support import dialogue_entries
from transbridge.application.dialogue.index import build_dialogue_index
from transbridge.application.dialogue.loading import DialogueIndexLoader
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui import context as context_module
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.workbench.step2 import Step2PreviewWidget

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("fail", [False, True])
def test_late_index_result_does_not_call_destroyed_controller_and_releases_worker(monkeypatch, fail):
    started, release = Event(), Event()

    def build(*_args, **_kwargs):
        started.set()
        assert release.wait(5)
        if fail:
            raise ValueError("late index failure")
        return build_dialogue_index(dialogue_entries())

    monkeypatch.setattr(DialogueIndexLoader, "build", build)
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    context = context_module.AppContext()
    context.variant_store = SimpleNamespace(dirty=False)
    collection = TranslationEntryCollection(dialogue_entries())
    context.add_slot("fixture.esp", context_module.CollectionSlot("Test", collection, esp_path="fixture.esp"))
    parent = QWidget()
    preview = Step2PreviewWidget(context, parent)
    workers, deliveries = [], []
    controller = DialogueEditorController(context, parent, preview, workers)
    # Capture stale dispatch without touching deleted widgets (which can abort Qt).
    monkeypatch.setattr(controller, "_loaded", lambda *_args: deliveries.append("loaded"))
    monkeypatch.setattr(controller, "_failed", lambda *_args: deliveries.append("failed"))
    try:
        assert started.wait(3)
        sip.delete(parent)
        assert sip.isdeleted(controller)
    finally:
        release.set()
        deadline = monotonic() + 5
        while workers and monotonic() < deadline:
            _APP.processEvents()
            QTest.qWait(1)
    assert not workers
    assert not deliveries
