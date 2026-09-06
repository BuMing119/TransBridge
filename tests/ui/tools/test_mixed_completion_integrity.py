from __future__ import annotations

from dataclasses import replace
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog
import pytest

from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator import _mixed_worker, task_session
from transbridge.ui.tools.ai_translator.source_execution import SourceOutcome
from transbridge.ui.tools.ai_translator.task_progress import AiTaskProgressWindow
from transbridge.ui.tools.ai_translator.task_scope import SourceTask

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("preview_action", ["no_polish", "reject_candidates", "cancel_dialog"])
def test_unified_mixed_completion_distinguishes_rejecting_candidates_from_cancelling_task(
    monkeypatch, preview_action: str
) -> None:
    entry = TranslationEntry("one", "one", "Source", "", 0, "NPC_:FULL")
    polish_entry = TranslationEntry("two", "two", "Other source", "旧译文", 1, "NPC_:FULL")
    collection = TranslationEntryCollection([entry, polish_entry])
    slot = CollectionSlot("plugin", collection)
    published = []

    class Context:
        active_version_identity = ("project", "variant")
        slots = {"plugin": slot}
        collection_changed = SimpleNamespace(emit=published.append)
        uses_authoritative_projection = False

        @property
        def collection(self):
            return slot.collection

    context = Context()
    persistence = MagicMock()
    persistence.commit_translation.return_value = {"ok": True}
    persistence.create_snapshot.return_value = {"ok": True}
    monkeypatch.setattr(task_session, "VersionPersistence", lambda *_args: persistence)
    spec = SimpleNamespace(mode="mixed", run_id="run", execution_profile=SimpleNamespace(preview_enabled=True))
    source = SourceTask(
        "plugin", "plugin", None, collection, (entry,), () if preview_action == "no_polish" else (polish_entry,)
    )
    session = task_session.TaskSession(context, (source,), spec)
    errors, ready = [], []
    session.capture_before(on_success=ready.append, on_error=errors.append)
    deadline = time.monotonic() + 5
    while session.is_busy and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.001)
    assert not session.is_busy and ready and not errors
    detached = session.tasks[0]
    detached.collection.add(replace(detached.translate_entries[0], translation="Translated", stage=1), overwrite=True)
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator._polish_preview_dialog._PolishPreviewDialog",
        lambda *_args, **_kwargs: SimpleNamespace(
            exec=lambda: (
                QDialog.DialogCode.Rejected if preview_action == "cancel_dialog" else QDialog.DialogCode.Accepted
            ),
            get_results=lambda: {"two": None},
            setWindowTitle=lambda _title: None,
        ),
    )
    window = AiTaskProgressWindow(SimpleNamespace(spec=spec), session, MagicMock())
    monkeypatch.setattr(window, "_render_reports", lambda: None)
    window._preparing = False
    window._worker = SimpleNamespace(was_cancelled=False)
    outcome = SourceOutcome(
        detached, translation=SimpleNamespace(success_count=1), polish={"two": SimpleNamespace(confidence=1)}
    )
    window._completed((outcome,))
    session.rollback_uncommitted()
    committed = preview_action != "cancel_dialog"
    assert session.completed is committed and session.can_save is committed
    assert context.collection.get("one").translation == ("Translated" if committed else "")
    assert context.collection.get("two").translation == "旧译文"
    assert collection.get("one").translation == ""
    assert published == ([context.collection] if committed else [])
    assert persistence.commit_translation.call_count == int(committed)
    if preview_action == "reject_candidates":
        assert outcome.polish_summary.rejected_entry_ids == ("two",)
    window._worker = None
    window.close()


def _worker(monkeypatch) -> _mixed_worker._MixedWorker:
    monkeypatch.setattr(_mixed_worker, "WorkflowLogStore", lambda *_args, **_kwargs: MagicMock())
    return _mixed_worker._MixedWorker(LLMConfig(), [object()], [object()], "parallel")


def test_parallel_stage_errors_reach_error_signal_instead_of_completed(monkeypatch) -> None:
    worker = _worker(monkeypatch)
    errors = []
    results = []
    worker.error.connect(errors.append)
    worker.finished.connect(results.append)

    def translate():
        raise RuntimeError("translator unavailable")

    def polish():
        raise ValueError("invalid term source")

    worker._do_translate = translate
    worker._do_polish = polish
    worker.run()

    assert results == []
    assert len(errors) == 1
    assert "translator unavailable" in errors[0]
    assert "invalid term source" in errors[0]


def test_failed_parallel_run_joins_successful_sibling_before_rollback(monkeypatch) -> None:
    worker = _worker(monkeypatch)
    sibling_started = threading.Event()
    release_sibling = threading.Event()
    order = []
    state = {"translation": "before"}
    results = []

    def translate():
        raise RuntimeError("translation setup failed")

    def polish():
        sibling_started.set()
        assert release_sibling.wait(3)
        state["translation"] = "late result"
        order.append("sibling completed")
        return SimpleNamespace()

    def rollback(_message):
        state["translation"] = "before"
        order.append("rollback")

    worker._do_translate = translate
    worker._do_polish = polish
    worker.error.connect(rollback, Qt.ConnectionType.DirectConnection)
    worker.finished.connect(results.append, Qt.ConnectionType.DirectConnection)
    runner = threading.Thread(target=worker.run)
    runner.start()
    try:
        assert sibling_started.wait(3)
        assert order == []
        assert runner.is_alive()
    finally:
        release_sibling.set()
        runner.join(3)
    assert not runner.is_alive()
    assert results == []
    assert order == ["sibling completed", "rollback"]
    assert state["translation"] == "before"
