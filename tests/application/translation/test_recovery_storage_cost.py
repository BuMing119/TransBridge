"""Cold recovery boundaries and observable I/O costs, using actual persisted records."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.application.translation.test_task_recovery import record
from transbridge.application.translation import task_recovery
from transbridge.application.translation.task_recovery import TaskRecoveryStore
from transbridge.converter.translation_entry import TranslationEntry


def full_record():
    value = record()
    key = value["sources"][0]["polish_keys"][0]
    from transbridge.application.io.identity import EntryKey

    entry = TranslationEntry(
        "one",
        "one",
        "Original",
        "Before",
        0,
        "",
        entry_key=EntryKey.from_dict(key),
        metadata=(("token", "game-token"), ("secret", "quest-secret"), ("headers", {"cookie": "business"})),
    )
    value.update(recovery_contract=2, overwrite=False, execution_runs=[value["task_id"]])
    value["sources"][0]["inputs"] = [
        {
            "entry_key": key,
            "original": entry.original,
            "translation": entry.translation,
            "stage": entry.stage,
            "context": entry.context or "",
            "revision": entry.revision.value,
            "entry": entry.to_dict(),
        }
    ]
    value["execution_inputs"] = {value["task_id"]: deepcopy(value["sources"])}
    return value


def test_business_entry_metadata_survives_cold_read_and_configuration_is_protected(tmp_path):
    value = full_record()
    TaskRecoveryStore(tmp_path).create(value)
    assert TaskRecoveryStore(tmp_path).load(value["task_id"]) == value
    for location in ("execution_config", "checkpoint_settings"):
        with pytest.raises(ValueError, match="credential"):
            TaskRecoveryStore(tmp_path / location).create({**value, location: {"nested": {"token": "credential"}}})
    bad = deepcopy(value)
    bad["sources"][0]["inputs"][0]["token"] = "not-an-entry-field"
    with pytest.raises(ValueError, match="credential"):
        TaskRecoveryStore(tmp_path / "bad").create(bad)


def test_fixed_sources_share_payload_and_state_updates_never_open_or_validate_inputs(tmp_path, monkeypatch):
    value = full_record()
    store = TaskRecoveryStore(tmp_path)
    store.create(value)
    manifest = json.loads(store._path(value["task_id"]).read_text(encoding="utf-8"))
    assert manifest["_input_refs"]["sources"] == manifest["_input_refs"]["execution_inputs"][value["task_id"]]
    assert len(tuple((tmp_path / "inputs").glob("*.json"))) == 1
    before = {path: path.read_bytes() for path in (tmp_path / "inputs").glob("*.json")}
    cold = TaskRecoveryStore(tmp_path)
    read = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path.parent.name != "inputs", "state update opened immutable input"
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    monkeypatch.setattr(task_recovery, "_validate", Mock(side_effect=AssertionError("full validation")))
    cold.update_metadata(value["task_id"], state="completed")
    unchanged = cold._path(value["task_id"]).read_bytes()
    monkeypatch.setattr(task_recovery, "atomic_json", Mock(side_effect=AssertionError("no-op write")))
    cold.update_metadata(value["task_id"], state="completed")
    assert cold._path(value["task_id"]).read_bytes() == unchanged
    assert {path: path.read_bytes() for path in before} == before


def test_legacy_inline_update_preserves_original_and_uses_current_reader(tmp_path):
    store = TaskRecoveryStore(tmp_path)
    value = full_record()
    path = store._path(value["task_id"])
    path.write_text(json.dumps(value), encoding="utf-8")
    original = path.read_bytes()
    assert store.load(value["task_id"]) == value
    store.update_metadata(value["task_id"], state="completed")
    assert json.loads((tmp_path / "legacy" / path.name).read_text(encoding="utf-8")) == json.loads(original)
    assert TaskRecoveryStore(tmp_path).load(value["task_id"])["sources"] == value["sources"]


def test_reused_corrupt_input_is_not_published_and_cold_content_validation_fails(tmp_path):
    value = full_record()
    store = TaskRecoveryStore(tmp_path)
    store.create(value)
    payload = next((tmp_path / "inputs").glob("*.json"))
    payload.write_text("{}", encoding="utf-8")
    summaries = TaskRecoveryStore(tmp_path).list_summaries()
    assert summaries[0]["task_id"] == value["task_id"]
    with pytest.raises(ValueError, match="checksum"):
        TaskRecoveryStore(tmp_path).load(value["task_id"])
    with pytest.raises(ValueError, match="checksum"):
        store.create({**value, "task_id": "second"})
    assert not store._path("second").exists()
    assert payload.read_text(encoding="utf-8") == "{}"


def test_mutable_payload_generations_are_bounded_and_failed_publish_keeps_previous(tmp_path, monkeypatch):
    value = full_record()
    store = TaskRecoveryStore(tmp_path)
    store.create(value)
    for count in range(5):
        store.update_metadata(value["task_id"], entry_statuses=[{"attempt": count}])
    assert len(tuple(path for path in (tmp_path / "state").rglob("*.json") if path.name != "owner.json")) == 1
    previous = store.load(value["task_id"])
    original = task_recovery.atomic_json

    def fail_manifest(path, data):
        if path == store._path(value["task_id"]):
            raise OSError("manifest publication failed")
        return original(path, data)

    monkeypatch.setattr(task_recovery, "atomic_json", fail_manifest)
    with pytest.raises(OSError, match="publication"):
        store.update_metadata(value["task_id"], entry_statuses=[{"attempt": 6}])
    assert store.load(value["task_id"]) == previous
    monkeypatch.setattr(task_recovery, "atomic_json", original)
    store.update_metadata(value["task_id"], entry_statuses=[{"attempt": 7}])
    assert len(tuple(path for path in (tmp_path / "state").rglob("*.json") if path.name != "owner.json")) == 1


def test_unchanged_successful_ledger_skips_result_io_and_detects_direct_replacement(tmp_path, monkeypatch):
    from tests.ui.tools.test_task_recovery_binding import setup_binding
    from transbridge.application.translation.task_entry_results import TaskEntryResults
    from transbridge.application.translation.task_result_recovery import save_recovery_results

    binding, request, session, _ = setup_binding(tmp_path)
    ledger = TaskEntryResults(session.tasks, request.run_id)
    key = next(iter(ledger.entries))
    ledger.entries[key] = replace(ledger.entries[key], status="succeeded", text="Result")
    store = binding.store
    original = store.save_results
    saves = Mock(wraps=original)
    monkeypatch.setattr(store, "save_results", saves)
    save_recovery_results(store, request.run_id, ledger.freeze())
    save_recovery_results(store, request.run_id, ledger.freeze())
    assert saves.call_count == 1
    ledger.entries[key] = replace(ledger.entries[key], decision_revision=1, decision="accepted")
    save_recovery_results(store, request.run_id, ledger.freeze())
    assert saves.call_count == 2
    assert store.load_results(request.run_id)["rows"][0]["decision_revision"] == 1
    binding.release()


def test_failed_result_write_is_retried_without_false_success_evidence(tmp_path, monkeypatch):
    from tests.ui.tools.test_task_recovery_binding import setup_binding
    from transbridge.application.translation.task_entry_results import TaskEntryResults
    from transbridge.application.translation.task_result_recovery import save_recovery_results

    binding, request, session, _ = setup_binding(tmp_path)
    ledger = TaskEntryResults(session.tasks, request.run_id)
    key = next(iter(ledger.entries))
    ledger.entries[key] = replace(ledger.entries[key], status="succeeded", text="Result")
    original = binding.store.save_results
    monkeypatch.setattr(binding.store, "save_results", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        save_recovery_results(binding.store, request.run_id, ledger)
    monkeypatch.setattr(binding.store, "save_results", original)
    save_recovery_results(binding.store, request.run_id, ledger)
    assert binding.store.load_results(request.run_id)["rows"][0]["text"] == "Result"
    binding.release()


def test_concurrent_saved_receipts_merge_without_losing_previous_entries(tmp_path):
    from tests.ui.tools.test_task_recovery_binding import setup_binding
    from transbridge.application.translation.task_entry_results import TaskEntryResults
    from transbridge.ui.tools.ai_translator.task_recovery_persistence import RecoveryUpdate

    binding, request, session, _ = setup_binding(tmp_path)
    initial = TaskEntryResults(session.tasks, request.run_id)
    updates = []
    for key in tuple(initial.entries)[:2]:
        ledger = initial.freeze()
        ledger.entries[key] = replace(
            ledger.entries[key], status="succeeded", text=f"saved-{key.local_key}", applied=True
        )
        updates.append(RecoveryUpdate(1, "partial", True, ledger, ledger))

    def execute(update):
        return update.execute(TaskRecoveryStore(binding.store.root), request.run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tuple(pool.map(execute, updates))
    record = TaskRecoveryStore(binding.store.root).load(request.run_id)
    assert len(record["applied_entries"]) == 2
    assert len(binding.store.load_results(request.run_id)["rows"]) == 2
    binding.release()


def test_saved_receipt_reuses_validated_scope_without_reopening_fixed_input(tmp_path, monkeypatch):
    value = full_record()
    store = TaskRecoveryStore(tmp_path)
    store.create(value)
    key = value["sources"][0]["polish_keys"][0]
    read = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path.parent.name != "inputs", "saved receipt reread fixed task input"
        return read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    store.update_metadata(
        value["task_id"],
        applied_entries=[
            {
                "entry_key": key,
                "original": "Original",
                "text": "saved",
                "stage": 1,
            }
        ],
    )
    # The full input remains fixed; only the mutable receipt generation changes.
    store.update_metadata(
        value["task_id"],
        applied_entries=[
            {
                "entry_key": key,
                "original": "Original",
                "text": "second-save",
                "stage": 1,
            }
        ],
    )
    with pytest.raises(ValueError, match="belong to its scope"):
        store.update_metadata(
            value["task_id"],
            applied_entries=[
                {
                    "entry_key": {**key, "local_key": "outside"},
                    "original": "Original",
                    "text": "bad",
                    "stage": 1,
                }
            ],
        )


def test_cold_scope_is_read_once_and_changed_source_reference_invalidates_evidence(tmp_path, monkeypatch):
    value = full_record()
    store = TaskRecoveryStore(tmp_path)
    store.create(value)
    cold = TaskRecoveryStore(tmp_path)
    original = Path.read_text
    input_reads = []

    def read(path, *args, **kwargs):
        if path.parent.name == "inputs":
            input_reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    row = {"entry_key": value["sources"][0]["polish_keys"][0], "original": "Original", "text": "saved", "stage": 1}
    cold.update_metadata(value["task_id"], applied_entries=[row])
    cold.update_metadata(value["task_id"], applied_entries=[{**row, "text": "second"}])
    assert len(input_reads) == 1
    # Another store installs a different fixed scope; the old membership proof
    # must not grant a receipt authority under its new content reference.
    store.update_metadata(
        value["task_id"],
        sources=[
            {
                **value["sources"][0],
                "polish_keys": [],
                "inputs": [],
            }
        ],
        applied_entries=[],
    )
    with pytest.raises(ValueError, match="belong to its scope"):
        cold.update_metadata(value["task_id"], applied_entries=[row])
