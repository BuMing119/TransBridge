"""Synthetic FR32 storage measurements without hardware-specific time limits."""

from collections import Counter
from itertools import count
import json
from pathlib import Path
import sqlite3
from threading import Event
from time import monotonic

from PyQt6.QtCore import QTimer
import pytest

from tests.integration.gui.test_project_paged_workbench import _application, _begin_open, _wait_until, _workbench
from tests.performance import measure
from transbridge.application.io import FormatId, Stage
from transbridge.application.io.identity import EntryKey, EntryRevision, SourceNamespace
from transbridge.application.io.mutation import EntrySnapshot
from transbridge.application.projects.storage_contracts import ExtractedSource, StoredEntry
from transbridge.application.projects.working_copy import ProjectWorkingCopy
from transbridge.persistence.project_store import reads
from transbridge.persistence.project_store.paged_reads import WORKBENCH_PAGE_SIZE
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.task_store import TaskStore
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.variant import VariantEntryState

_PROJECT = ProjectId("scaling-fixture")
_VARIANT = VariantId("main")
_SOURCE_COUNT = 80
_PAGE_SIZE = 40


def _fixture(workspace, rows, source_count=_SOURCE_COUNT):
    store = ProjectStore(project_path(workspace, _PROJECT))
    sources = []
    for source_index in range(source_count):
        namespace = SourceNamespace(f"source:benchmark-{source_index:02}")
        entries = []
        for row in range(rows // source_count):
            key = EntryKey(namespace, f"entry-{row:04}")
            original = f"Synthetic original {source_index:02}/{row:04}"
            translation = f"Initial translation {row:04}"
            state = VariantEntryState(key, translation, stage=Stage.CHECKED)
            snapshot = EntrySnapshot(
                key,
                key.local_key,
                original,
                translation,
                state.stage.value,
                "NPC_:FULL",
                (),
                EntryRevision(),
                (),
                (),
            )
            entries.append(StoredEntry(snapshot, state))
        sources.append(
            ExtractedSource(namespace, f"Source-{source_index:02}.json", FormatId.JSON_DSD, "a" * 64, tuple(entries))
        )
    store.create(_PROJECT, "Synthetic translations", _VARIANT, "Main", sources)
    return store


def _add_task_history(store, count):
    recovery = store.recovery()
    for number in range(count):
        recovery.create({
            "task_id": f"historical-{number:04}",
            "project_id": _PROJECT.value,
            "variant_id": _VARIANT.value,
            "mode": "download",
            "config_digest": "synthetic",
            "state": "completed",
            "created_at": "2026-11-09T00:00:00+08:00",
            "sources": [],
            "result_kind": "download",
            "results_only": True,
        })
    assert len(recovery.list_summaries()) == count


def _changed_page_bytes(before, after):
    page_size = int.from_bytes(before[16:18], "big")
    if page_size == 1:
        page_size = 65536
    return page_size * sum(
        before[start : start + page_size] != after[start : start + page_size]
        for start in range(0, max(len(before), len(after)), page_size)
    )


def _measure_store(store, monkeypatch):
    connect = sqlite3.connect
    connections = []
    journal = Path(str(store.path) + "-journal")

    class MeteredConnection(sqlite3.Connection):
        def commit(self):
            if journal.is_file():
                self.journal_bytes = max(self.journal_bytes, journal.stat().st_size)
            super().commit()

        def close(self):
            connections.append((self.total_changes, self.vm_steps, self.journal_bytes))
            super().close()

        def progress(self):
            self.vm_steps += 100
            return 0

    def open_measured(database, *args, **kwargs):
        assert "tasks.sqlite3" not in str(database)
        connection = connect(database, *args, **kwargs, factory=MeteredConnection)
        connection.vm_steps = 0
        connection.journal_bytes = 0
        connection.set_progress_handler(connection.progress, 100)
        return connection

    def open_first_page():
        reopened = ProjectStore(store.path)
        assert reopened.summary().active_variant == _VARIANT
        assert len(reopened.sources()) == _SOURCE_COUNT
        page = reopened.read_page(_VARIANT, limit=_PAGE_SIZE)
        assert len(page) == _PAGE_SIZE
        return page

    def operations():
        return tuple(sum(values) for values in zip(*connections, strict=True))

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", open_measured)
        page = open_first_page()
        read_changes, read_steps, _read_journal = operations()
        assert read_changes == 0
        open_seconds = measure.sample_time(open_first_page, warmup=0, repetitions=5)
        key = page[0].state.entry_key
        working = ProjectWorkingCopy(store, _VARIANT)
        state_number = count(0)

        def save_one():
            # Keep the UTF-8 payload length fixed across samples.
            working.edit(key, translation=f"Edited  translation {next(state_number):04}", stage=Stage.CHECKED)
            assert len(working.save().entries) == 1

        before = store.path.read_bytes()
        connections.clear()
        save_one()
        writes, _save_steps, journal_bytes = operations()
        changed_page_bytes = _changed_page_bytes(before, store.path.read_bytes())
        save_seconds = measure.sample_time(save_one, warmup=0, repetitions=5)

        connections.clear()
        before_clean = store.path.read_bytes()
        clean_seconds = measure.sample_time(working.save, warmup=0, repetitions=5)
        assert operations()[0::2] == (0, 0)
        assert store.path.read_bytes() == before_clean
        assert store.read_page(_VARIANT, offset=1, limit=_PAGE_SIZE - 1) == page[1:]
    return {
        "open_seconds": open_seconds,
        "save_seconds": save_seconds,
        "clean_seconds": clean_seconds,
        "open_vm_steps": read_steps,
        "changed_rows": writes,
        "changed_page_bytes": changed_page_bytes,
        "journal_bytes_before_commit": journal_bytes,
        "project_bytes": store.path.stat().st_size,
    }


@pytest.mark.slow
def test_fixed_size_edits_and_first_page_are_independent_of_project_and_task_history_size(tmp_path, monkeypatch):
    measurements = []
    for row_count in (10_000, 100_000):
        workspace = tmp_path / str(row_count)
        project = _fixture(workspace, row_count)
        tasks = TaskStore(workspace / "task-store-v1")
        measurements.append({"rows": row_count, "tasks": 0, **_measure_store(project, monkeypatch)})
        if row_count == 100_000:
            _add_task_history(tasks, 1000)
            measurements.append({"rows": row_count, "tasks": 1000, **_measure_store(project, monkeypatch)})
    print(json.dumps(measurements))
    small, large, with_history = measurements
    for item in measurements:
        assert item["changed_rows"] == 3
        assert item["journal_bytes_before_commit"] > 0
        assert item["changed_page_bytes"] > 0
    assert large["open_vm_steps"] <= small["open_vm_steps"] * 2 + 1000
    assert with_history["open_vm_steps"] == large["open_vm_steps"]
    assert large["journal_bytes_before_commit"] <= small["journal_bytes_before_commit"] * 2
    assert with_history["journal_bytes_before_commit"] == large["journal_bytes_before_commit"]
    assert large["changed_page_bytes"] <= small["changed_page_bytes"] * 2
    assert with_history["changed_page_bytes"] == large["changed_page_bytes"]


@pytest.fixture(scope="module")
def gui_app():
    with pytest.MonkeyPatch.context() as patch:
        yield _application(patch)


@pytest.mark.slow
@pytest.mark.parametrize("source_count", [_SOURCE_COUNT, 1], ids=["80-sources", "single-source"])
def test_gui_first_page_ready_on_100000_rows(tmp_path, monkeypatch, gui_app, source_count):
    app = gui_app
    workspace = tmp_path / "gui-bench"
    project = _fixture(workspace, 100_000, source_count)
    active_rows = 100_000 // source_count
    source = project.sources()[0]
    decode = reads.decode_entry
    decoded = Counter()
    allow_full_read = Event()

    def observe_entry(row):
        namespace = row["namespace"]
        if decoded[namespace] == WORKBENCH_PAGE_SIZE:
            assert allow_full_read.wait(30)
        decoded[namespace] += 1
        return decode(row)

    monkeypatch.setattr(reads, "decode_entry", observe_entry)
    samples = []
    for index in range(6):
        with _workbench(workspace, app) as (host, coordinator, _services):
            decoded.clear()
            allow_full_read.clear()
            heartbeats = []
            rss = []

            def heartbeat():
                heartbeats.append(monotonic())
                if measure.psutil is not None:
                    rss.append(measure.current_rss_bytes())

            timer = QTimer()
            timer.setInterval(5)
            timer.timeout.connect(heartbeat)
            heartbeat()
            timer.start()
            started = monotonic()
            try:
                failures = _begin_open(host, coordinator, project.path)
                _wait_until(app, lambda: host.project_open_worker is None, timeout=30)
                preview = host.workbench.preview
                assert not failures, failures
                assert not host.context.project_view_failed
                assert preview._table.isEnabled()
                assert preview._table.model().rowCount() == active_rows
                assert preview._table.entry_at_row(0) is not None
                assert preview._table.entry_at_row(active_rows - 1) is None
                assert preview.get_filtered_count() == active_rows
                elapsed = monotonic() - started
                heartbeat()
                assert decoded == {source.namespace.value: WORKBENCH_PAGE_SIZE}
                largest_gap = max(end - start for start, end in zip(heartbeats, heartbeats[1:]))
                opening_rss = max(rss) if rss else None
                heartbeats.clear()
                rss.clear()
                heartbeat()
                ready = monotonic()
                allow_full_read.set()
                _wait_until(app, lambda: preview._paging.resident_ready, timeout=60)
                preload_elapsed = monotonic() - ready
                heartbeat()
                assert len(heartbeats) >= 3
                assert decoded.keys() == {source.namespace.value}
                assert preview._table.entry_at_row(active_rows - 1).original == (
                    f"Synthetic original 00/{active_rows - 1:04}"
                )
                assert len(preview.filtered_entries()) == active_rows
                preload_rss = max(rss) if rss else None
                preload_gap = max(end - start for start, end in zip(heartbeats, heartbeats[1:]))
                decoded.clear()
                heartbeats.clear()
                rss.clear()
                heartbeat()
                ready = monotonic()
                preview._filters_view.search_original.setText("Synthetic original 00/000")
                _wait_until(app, lambda: preview._table.model().rowCount() == 10, timeout=60)
                query_elapsed = monotonic() - ready
                heartbeat()
                assert not decoded
                assert len(preview.filtered_entries()) == 10
            finally:
                allow_full_read.set()
                timer.stop()
            samples.append({
                "sample": "first_open_after_fixture_creation" if index == 0 else "reload",
                "sources": source_count,
                "active_source_rows": active_rows,
                "first_page_seconds": elapsed,
                "full_active_source_preload_seconds": preload_elapsed,
                "full_active_source_filter_seconds": query_elapsed,
                "max_heartbeat_gap_to_first_page_seconds": largest_gap,
                "max_heartbeat_gap_during_preload_seconds": preload_gap,
                "max_heartbeat_gap_during_filter_seconds": max(
                    end - start for start, end in zip(heartbeats, heartbeats[1:])
                ),
                "sampled_peak_open_rss_bytes": opening_rss,
                "sampled_peak_preload_rss_bytes": preload_rss,
                "sampled_peak_filter_rss_bytes": max(rss) if rss else None,
                "first_page_rows": WORKBENCH_PAGE_SIZE,
            })
    print(json.dumps(samples))
