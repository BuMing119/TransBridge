"""Large offline finalization through real reference stores and source matching."""

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from transbridge.ai_translator.project_terminology_runtime import ProjectTerminologyBinding
from transbridge.ai_translator.term_database import TermDatabaseManager
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.ai_translator.translation_entry_outcomes import TranslationEntryOutcome
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator.source_execution import SourceExecutor, SourceOutcome
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency
from transbridge.ui.tools.ai_translator.task_scope import SourceTask


@pytest.mark.slow
def test_twenty_thousand_entries_reuse_references_and_finish_both_sources(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("transbridge.config.paths.get_data_dir", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda stem: str(tmp_path / stem)))
    calls = []

    def remote(_manager):
        calls.append(True)
        return [TermEntry(f"Location {index}", f"地点{index}号", "paratranz") for index in range(1000)]

    monkeypatch.setattr(TermDatabaseManager, "_load_paratranz", remote)
    config = LLMConfig(term_priority=["paratranz"], enable_semantic_match=False, enable_post_process=False)
    request = SimpleNamespace(
        run_id="large-finalization",
        config=config,
        terminology_binding=ProjectTerminologyBinding(),
        spec=SimpleNamespace(owner=SimpleNamespace(project_id="project", variant_id="variant")),
    )
    guard = TaskConsistency(request)
    pause = threading.Event()
    pause.set()
    progress = []
    executor = SourceExecutor(
        request,
        stop_event=threading.Event(),
        pause_event=pause,
        shared_terms={},
        terms_lock=threading.Lock(),
        progress=lambda *event: progress.append(event),
        log=lambda *_: None,
        consistency=guard,
    )
    executor.shared_term_pool.publish([
        (f"Location {index}", f"地点{index}号", "auto_name", "") for index in range(1000)
    ])
    outcomes = []
    for source in ("A", "B"):
        entries = tuple(
            TranslationEntry(
                str(index),
                str(index),
                f"Visit Location {index % 1000}.",
                f"前往地点{index % 1000}号。",
                2,
                "BOOK:DESC",
                entry_key=EntryKey(SourceNamespace(source), str(index)),
            )
            for index in range(10000)
        )
        task = SourceTask(source, source, f"{source}.esp", TranslationEntryCollection(entries), entries, ())
        results = {
            entry.identity: TranslationEntryOutcome(entry.identity, "succeeded", entry.translation, entry.stage)
            for entry in entries
        }
        outcomes.append(SourceOutcome(task, translation=SimpleNamespace(entry_outcomes=results)))
        manager = TermDatabaseManager(
            config,
            task.esp_path,
            shared_term_pool=executor.shared_term_pool,
            reference_store=executor.reference_store,
            reference_source=source,
        )
        manager.load_all()
    assert len(calls) == 1

    app = QApplication.instance() or QApplication([])
    heartbeat = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: heartbeat.append(time.perf_counter()))
    timer.start()
    started = time.perf_counter()
    heartbeat.append(started)
    try:
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(executor.close_terms, outcomes)
            while not future.done():
                app.processEvents()
                time.sleep(0.001)
            closed = future.result()
        app.processEvents()
    finally:
        timer.stop()
    elapsed = time.perf_counter() - started
    heartbeat.append(time.perf_counter())
    largest_gap = max(later - before for before, later in zip(heartbeat, heartbeat[1:]))
    assert len(heartbeat) > 5
    assert largest_gap < 2, "background finalization starved the GUI event loop"
    assert all(result.successful for result in closed)
    assert len(calls) == 1, "finalization must not reload remote references"
    assert not any(event[1] == "term_closure" for event in progress)
    for source in ("A", "B"):
        events = [event for event in progress if event[0] == source]
        assert events[0][1] == "term_closure_load"
        assert any(event[1] == "term_closure_scan" and 0 < event[2] < 10000 for event in events)
        assert events[-1][1:4] == ("term_closure_done", 10000, 10000)
    assert len(progress) < 500
    with capsys.disabled():
        print(
            f"\nreal_finalization entries=20000 terms=1000 sources=2 seconds={elapsed:.3f} "
            f"events={len(progress)} max_gui_gap_ms={largest_gap * 1000:.1f}"
        )
