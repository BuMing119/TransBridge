from __future__ import annotations

from dataclasses import replace
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog
import pytest

from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator import _mixed_worker, result_view, version_snapshot
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.result_presenter import ResultPresenter

_APP = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("reject_polish", [False, True])
def test_completed_translation_survives_missing_or_rejected_polish(monkeypatch, reject_polish: bool) -> None:
    entry = TranslationEntry("one", "one", "Source", "", 0, "NPC_:FULL")
    collection = TranslationEntryCollection([entry])
    published = []
    context = SimpleNamespace(
        active_version_identity=("project", "variant"),
        collection=collection,
        collection_changed=SimpleNamespace(emit=published.append),
        uses_authoritative_projection=False,
    )
    persistence = MagicMock()
    persistence.commit_translation.return_value = {"ok": True}
    monkeypatch.setattr(version_snapshot, "VersionPersistence", lambda *_args: persistence)
    session = version_snapshot.AiVersionSnapshotSession(context, SimpleNamespace(mode="mixed", run_id="run"))
    collection.add(replace(entry, translation="Translated", stage=1), overwrite=True)
    monkeypatch.setattr(result_view, "show_window_mixed_report", lambda *_args: object())
    if reject_polish:
        monkeypatch.setattr(
            "transbridge.ui.tools.ai_translator._polish_preview_dialog._PolishPreviewDialog",
            lambda *_args, **_kwargs: SimpleNamespace(exec=lambda: QDialog.DialogCode.Rejected),
        )
    window = SimpleNamespace(
        _ctx=context,
        _theme_view=None,
        _version_snapshot_session=session,
        _active_mixed_polish_entries=[],
        _active_mixed_preview=reject_polish,
        _result_presenter=ResultPresenter(),
    )
    result = {
        "translate": SimpleNamespace(success_count=1),
        "polish": SimpleNamespace(candidates={}) if reject_polish else None,
    }

    AITranslatorWindow._on_mixed_finished(window, result)
    session.rollback_uncommitted()  # Closing the configuration window follows this path.

    assert session.completed and session.can_save
    assert callable(result_view._save_translation_action(window))
    assert collection.get("one").translation == "Translated"
    assert published == [collection]
    committed = persistence.commit_translation.call_args.args[0]
    assert [(item.translation, item.stage) for item in committed] == [("Translated", 1)]


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
