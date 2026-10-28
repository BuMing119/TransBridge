from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.application.translation.task_entry_results import TaskEntryResults
from transbridge.application.translation.task_history import TaskHistoryStore
from transbridge.application.translation.task_scope import SourceTask
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.ui.tools.ai_translator.task_record_inputs import RecordSource
from transbridge.ui.tools.ai_translator.task_record_persistence import TaskRecordPersistence


def inputs():
    entry = TranslationEntry("one", "one", "Hello", "你好", 1, "")
    task = SourceTask("source", "Source", None, None, (), (entry,))
    ledger = TaskEntryResults((task,), "run")
    return (
        (RecordSource("source", "Source", "", ()),),
        ledger,
        SimpleNamespace(run_id="run", config=SimpleNamespace(mixed_execution_order="serial")),
    )


def persist(writer, directory, sources, ledger, request, *, state="running", **overrides):
    return writer.save(
        directory,
        sources,
        ledger.freeze(),
        request,
        created_at="2026-10-26T00:00:00+00:00",
        state=state,
        **dict(applied=False, saved=False, project_saved=False, snapshot_saved=False, version_label="v", **overrides),
    )


def test_status_and_noop_reuse_durable_report_payload(tmp_path, monkeypatch):
    from transbridge.ui.tools.ai_translator import task_record_inputs

    sources, ledger, request = inputs()
    writer = TaskRecordPersistence()
    record, error = persist(writer, tmp_path, sources, ledger, request)
    assert not error
    payload = next(tmp_path.glob("*/record-*.json"))
    before = payload.read_bytes()
    metadata = payload.parent / "metadata.json"

    def unexpected(*args, **kwargs):
        pytest.fail("unchanged results must not rebuild report payload")

    monkeypatch.setattr(task_record_inputs, "ledger_outcomes", unexpected)
    record, error = persist(writer, tmp_path, sources, ledger, request, state="completed")
    assert not error and record["ended_at"]
    assert TaskHistoryStore(tmp_path).load("run")["state"] == "completed"
    assert payload.read_bytes() == before
    assert tuple(tmp_path.glob("*/record-*.json")) == (payload,)
    saved_metadata = metadata.read_bytes()
    previous_time = metadata.stat().st_mtime_ns
    again, error = persist(writer, tmp_path, sources, ledger, request, state="completed")
    assert not error and again == record
    assert metadata.read_bytes() == saved_metadata and metadata.stat().st_mtime_ns == previous_time


@pytest.mark.parametrize("change", ["entry", "cancelled", "source"])
def test_changed_report_content_publishes_new_generation(tmp_path, change):
    sources, ledger, request = inputs()
    writer = TaskRecordPersistence()
    assert not persist(writer, tmp_path, sources, ledger, request)[1]
    old_payload = next(tmp_path.glob("*/record-*.json"))
    state = "running"
    if change == "entry":
        key = next(iter(ledger.entries))
        ledger.entries[key] = replace(ledger.entries[key], text="新候选", status="succeeded")
    elif change == "source":
        sources = (replace(sources[0], error="source failed"),)
    else:
        state = "cancelled"
    record, error = persist(writer, tmp_path, sources, ledger, request, state=state)
    assert not error and not old_payload.exists()
    assert len(tuple(tmp_path.glob("*/record-*.json"))) == 1
    restored = TaskHistoryStore(tmp_path).load("run")
    assert restored["sources"] == record["sources"]
    if change == "entry":
        assert restored["sources"][0]["snapshot"]["entries"][0]["candidate"] == "新候选"


def test_failed_metadata_write_retains_candidate_and_can_retry(tmp_path, monkeypatch):
    sources, ledger, request = inputs()
    writer = TaskRecordPersistence()
    record, error = persist(writer, tmp_path, sources, ledger, request)
    assert not error
    original = TaskHistoryStore.update_metadata

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(TaskHistoryStore, "update_metadata", fail)
    failed_record, error = persist(writer, tmp_path, sources, ledger, request, state="completed")
    assert "disk full" in error and failed_record["sources"] == record["sources"]
    assert TaskHistoryStore(tmp_path).load("run")["state"] == "running"
    monkeypatch.setattr(TaskHistoryStore, "update_metadata", original)
    assert not persist(writer, tmp_path, sources, ledger, request, state="completed")[1]
    assert TaskHistoryStore(tmp_path).load("run")["state"] == "completed"


def test_stale_writer_does_not_change_newer_report_status(tmp_path):
    sources, ledger, request = inputs()
    writer = TaskRecordPersistence()
    record, error = persist(writer, tmp_path, sources, ledger, request)
    assert not error
    newer = {**record, "state": "partial", "version_label": "newer writer"}
    TaskHistoryStore(tmp_path).save(newer)
    _record, error = persist(writer, tmp_path, sources, ledger, request, state="completed")
    assert "payload changed" in error
    restored = TaskHistoryStore(tmp_path).load("run")
    assert restored["state"] == "partial" and restored["version_label"] == "newer writer"
