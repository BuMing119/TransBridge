"""Discovery filters metadata before selected content is read in a worker."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.application.translation.test_recovery_storage_cost import full_record
from tests.ui.tools import test_task_recovery_discovery as recovery_fixtures
from tests.ui.tools.test_task_recovery_discovery import record, wait_until
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.ui.tools.ai_translator.task_recovery_prompt import load_selected_recovery, read_recovery_records

qapp = recovery_fixtures.qapp
setup_registry = recovery_fixtures.setup_registry


def test_discovery_reads_each_manifest_once_and_no_fixed_or_result_payload(tmp_path, monkeypatch):
    root = tmp_path / "ai-task-recovery"
    store = TaskRecoveryStore(root)
    for value in (
        full_record(),
        {**full_record(), "task_id": "completed", "state": "completed"},
        {**full_record(), "task_id": "other", "variant_id": "other"},
    ):
        store.create(value)
    reads = []
    original = Path.read_text

    def read(path, *args, **kwargs):
        assert path.parent.name not in {"inputs", "results"}
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    records = read_recovery_records(tmp_path, project_id="project", variant_id="variant", states={"running"})
    assert [value["task_id"] for value in records] == ["task-one"]
    assert len(reads) == len(set(reads)) == 3
    assert "inputs" not in records[0]["sources"][0]
    assert "results" not in records[0]


def test_bad_content_stays_listed_but_is_rejected_at_explicit_selection(tmp_path):
    store = TaskRecoveryStore(tmp_path / "ai-task-recovery")
    store.create(full_record())
    payload = next((store.root / "inputs").glob("*.json"))
    payload.write_text("{}", encoding="utf-8")
    (summary,) = read_recovery_records(tmp_path)
    with pytest.raises(ValueError, match="checksum"):
        load_selected_recovery(tmp_path, summary)
    assert payload.read_text(encoding="utf-8") == "{}"


def test_async_selection_validates_only_selected_task_and_rechecks_target(setup_registry, monkeypatch):
    from transbridge.ui.tools.ai_translator import task_recovery_prompt

    app, ctx, registry, store = setup_registry
    store.create(record("selected", state="partial"))
    store.create(record("unused", state="completed"))
    summaries = {value["task_id"]: value for value in read_recovery_records(store.root.parent)}
    loads = Mock(wraps=task_recovery_prompt.load_selected_recovery)
    monkeypatch.setattr(task_recovery_prompt, "load_selected_recovery", loads)
    delivered = []
    registry.recovery_requested.connect(lambda value, restart: delivered.append(value))
    assert registry.request_recovery(summaries["selected"])
    ctx.active_version_identity = ("project", "another")
    wait_until(app, lambda: not registry._recovery_workers)
    assert not delivered
    assert loads.call_count == 1
    assert not registry.is_recovery_active("selected")


def test_selected_result_failure_preserves_other_summaries_and_reports_error(setup_registry, monkeypatch):
    from transbridge.application.translation import task_result_recovery

    app, _ctx, registry, store = setup_registry
    store.create(record("damaged", state="partial"))
    store.create(record("healthy", state="partial"))
    summaries = {value["task_id"]: value for value in read_recovery_records(store.root.parent)}
    monkeypatch.setattr(task_result_recovery, "load_recovery_results", Mock(side_effect=ValueError("bad result")))
    assert registry.request_recovery(summaries["damaged"])
    wait_until(app, lambda: not registry._recovery_workers)
    assert "bad result" in registry.recovery_error
    assert not summaries["damaged"]["supported"]
    assert summaries["healthy"]["supported"]
    assert store.load("damaged")["state"] == "partial"
