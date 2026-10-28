from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.ui.tools.test_task_recovery_binding import setup_binding
from tests.ui.tools.test_task_recovery_discovery import record, setup_registry
from tests.ui.tools.test_unified_task_runtime import launch, qapp
from transbridge.ui.tools.ai_translator import task_recovery_binding, task_runtime
from transbridge.ui.tools.ai_translator.task_recovery_binding import TaskRecoveryBinding, recovery_sources
from transbridge.ui.tools.ai_translator.task_recovery_prompt import read_recovery_records, recovery_summary

__all__ = ["launch", "qapp", "setup_registry"]


@pytest.mark.parametrize("change", ["source", "key", "original", "translation", "stage", "context", "snapshot"])
def test_recovery_preserves_frozen_scope_despite_live_changes(tmp_path, change):
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    binding, request, _, window = setup_binding(tmp_path)
    frozen = deepcopy(binding.record["sources"][0]["inputs"][0])
    entry = next(iter(window._ctx.slots["first"].collection))
    if change == "source":
        del window._ctx.slots["first"]
    elif change == "key":
        window._ctx.slots["first"].collection = TranslationEntryCollection([])
    elif change == "snapshot":
        binding.record["sources"][0]["inputs"] = []
    else:
        object.__setattr__(entry, change, 9 if change == "stage" else "changed")
    if change == "snapshot":
        with pytest.raises(ValueError, match="缺少词条"):
            recovery_sources(window, request.config)
    else:
        tasks = recovery_sources(window, request.config)
        assert len(tasks) == 3
        restored = tasks[0].entries[0]
        assert restored.to_dict() == frozen["entry"]
        assert restored is not entry
    assert binding.store.load("run")["state"] == "preparing"


def test_saved_after_content_can_recover_when_metadata_save_was_interrupted(tmp_path):
    binding, request, _, window = setup_binding(tmp_path)
    entry = next(iter(window._ctx.slots["first"].collection))
    before = binding.record["sources"][0]["inputs"][0]
    binding.record["results"] = {
        "rows": [
            {
                "entry_key": entry.identity.to_dict(),
                "source": "first",
                "before": before,
                "status": "succeeded",
                "decision": "accepted",
                "text": "已写入项目的候选",
                "stage": 1,
            }
        ]
    }
    object.__setattr__(entry, "translation", "已写入项目的候选")
    object.__setattr__(entry, "stage", 1)
    restored = recovery_sources(window, request.config)[0].entries[0]
    assert (restored.translation, restored.stage) == (before["translation"], before["stage"])
    assert (entry.translation, entry.stage) == ("已写入项目的候选", 1)
    binding.record["results"]["rows"][0]["decision"] = "pending"
    assert recovery_sources(window, request.config)[0].entries[0].to_dict() == restored.to_dict()


def test_mixed_recovery_preserves_both_actions_and_overwrite(tmp_path):
    old, request, session, _ = setup_binding(tmp_path)
    old.release()
    request.run_id = "mixed"
    request.spec.mode, request.spec.overwrite = "mixed", True
    first = session.tasks[0]
    session.tasks = (replace(first, translate_entries=first.polish_entries, polish_entries=()), *session.tasks[1:])
    binding = TaskRecoveryBinding(request, session)
    assert binding.record["overwrite"]
    assert binding.record["sources"][0]["translate_keys"]
    assert binding.record["sources"][1]["polish_keys"]
    assert all(source["checkpoint_stem"].startswith("project-source-") for source in binding.record["sources"])


def test_new_manifest_continuation_keeps_scope_and_adds_execution_identity(tmp_path):
    old, request, session, _ = setup_binding(tmp_path)
    original_sources = deepcopy(old.record["sources"])
    old.release()
    request.recovery_task_id, request.run_id = "run", "next-run"
    session.tasks = session.tasks[1:]
    resumed = TaskRecoveryBinding(request, session)
    assert resumed.record["sources"] == original_sources
    assert resumed.record["execution_runs"] == ["run", "next-run"]
    assert resumed.record["task_id"] == "run"
    assert resumed.record["execution_inputs"]["run"] == original_sources
    assert len(resumed.record["execution_inputs"]["next-run"]) == 2


def test_recovery_records_reference_versions_without_requiring_them_to_remain_current(tmp_path):
    import json

    from transbridge.application.translation.terminology_run_snapshot import TerminologyRunSnapshotRef

    old, request, session, _ = setup_binding(tmp_path)
    assert "reference_snapshot" not in old.record
    old.release()

    def reference(version):
        return TerminologyRunSnapshotRef(
            "project",
            "variant",
            "ready",
            version,
            "digest",
            f"project:variant:{version}:digest",
            "2026-10-24T00:00:00Z",
        )

    request.run_id, request.config.api_key = "reference-run", "never-persist-this-secret"
    request.terminology_binding = SimpleNamespace(snapshot_ref=reference("first"))
    first = TaskRecoveryBinding(request, session)
    original_reference = first.record["reference_snapshot"]
    assert original_reference == reference("first").to_dict()
    first.release()
    request.recovery_task_id, request.run_id = "reference-run", "next-reference-run"
    request.terminology_binding = SimpleNamespace(snapshot_ref=reference("updated"))
    continued = TaskRecoveryBinding(request, session)
    assert continued.record["reference_snapshot"] == original_reference
    assert continued.record["execution_references"] == {
        "reference-run": reference("first").to_dict(),
        "next-reference-run": reference("updated").to_dict(),
    }
    assert "never-persist-this-secret" not in json.dumps(continued.record)
    continued.release()


def test_saved_result_does_not_silently_skip_changed_context(tmp_path):
    binding, request, _, window = setup_binding(tmp_path)
    entry = next(iter(window._ctx.slots["first"].collection))
    binding.record["applied_entries"] = [
        {
            "entry_key": entry.identity.to_dict(),
            "original": entry.original,
            "text": entry.translation,
            "stage": entry.stage,
        }
    ]
    object.__setattr__(entry, "context", "A different speaker")
    restored = recovery_sources(window, request.config)[0].entries[0]
    assert restored.context == "NPC_:FULL"
    assert entry.context == "A different speaker"
    from transbridge.application.translation.result_merge import ResultConflictPolicy, merge_result_entries
    from transbridge.ui.tools.ai_translator.task_session_merge import entry_state

    plan = merge_result_entries(
        {restored.identity: entry_state(restored)},
        {entry.identity: entry_state(entry)},
        {restored.identity: replace(entry_state(restored), translation="AI result", stage=3)},
        policy=ResultConflictPolicy.PREFER_INCOMING,
    )
    assert not plan.applied and plan.pending[0].reason == "source_changed_or_missing"


@pytest.mark.parametrize("damage", ["identity", "duplicate_scope", "entry_envelope"])
def test_fixed_recovery_still_rejects_wrong_identity_or_corrupt_input_structure(tmp_path, damage):
    binding, request, _, window = setup_binding(tmp_path)
    if damage == "identity":
        window._ctx.active_version_identity = ("different-project", "variant")
    elif damage == "duplicate_scope":
        binding.record["sources"].append(deepcopy(binding.record["sources"][0]))
    else:
        binding.record["sources"][0]["inputs"][0]["entry"]["translation"] = "tampered"
    with pytest.raises(ValueError, match="项目或版本已变化|重复条目身份|封套与固定输入不匹配"):
        recovery_sources(window, request.config)


def test_legacy_continuation_creates_new_manifest_with_current_config(tmp_path):
    old, request, session, window = setup_binding(tmp_path)
    old.release()
    legacy = {**old.record, "task_id": "legacy", "legacy": True, "config_digest": "unknown"}
    legacy["execution_runs"] = ["legacy"]
    legacy["sources"][0]["checkpoint_stems"] = ["OriginalPlugin"]
    entry = session.tasks[0].entries[0]
    original_revision = entry.revision.value
    object.__setattr__(entry, "revision", type(entry.revision)(original_revision + 1))
    request.run_id, request.recovery_record = "new-task", legacy
    request.config.model = "current-model"
    window._recovery_selection = (legacy, False)
    assert len(recovery_sources(window, request.config)) == 3
    binding = TaskRecoveryBinding(request, session)
    assert binding.record["task_id"] == "new-task"
    assert binding.record["origin_task_id"] == "legacy"
    assert binding.record["execution_runs"] == ["legacy", "new-task"]
    assert binding.record["execution_config"]["model"] == "current-model"
    assert binding.record["execution_inputs"]["legacy"][0]["inputs"][0]["revision"] == original_revision
    assert binding.record["execution_inputs"]["new-task"][0]["inputs"][0]["revision"] == original_revision + 1
    assert "OriginalPlugin" in binding.record["sources"][0]["checkpoint_stems"]
    with pytest.raises(ValueError, match="Cannot read task recovery"):
        binding.store.load("legacy")
    request.recovery_task_id, request.run_id = "new-task", "another-process"
    with pytest.raises(RuntimeError, match="正在|运行|恢复|占用"):
        TaskRecoveryBinding(request, session)
    binding.release()
    resumed = TaskRecoveryBinding(request, session)
    assert resumed.record["origin_task_id"] == "legacy"
    resumed.release()


def test_legacy_continue_opens_configuration_without_calling_ai(qapp, monkeypatch):
    window = SimpleNamespace(on_start=Mock(), close=Mock())
    monkeypatch.setattr(task_recovery_binding, "configure_recovery", Mock())
    show = Mock()
    monkeypatch.setattr("transbridge.ui.windowing.show_and_activate", show)
    task_recovery_binding.open_recovery(window, {"task_id": "legacy", "legacy": True})
    show.assert_called_once_with(window)
    window.on_start.assert_not_called()
    window.close.assert_not_called()


def test_pending_recovery_and_launch_reservations_prevent_double_start(setup_registry):
    _app, _ctx, registry, _store = setup_registry
    requests = []
    registry.recovery_requested.connect(lambda *args: requests.append(args))
    assert registry.request_recovery(record())
    assert not registry.request_recovery(record())
    first, second = object(), object()
    assert registry.reserve_recovery_launch("resume", first)
    assert not registry.reserve_recovery_launch("resume", second)
    registry.release_recovery_launch("resume", second)
    assert registry.is_recovery_active("resume")
    registry.release_recovery_launch("resume", first)
    assert registry.request_recovery(record())
    assert len(requests) == 2


def test_running_legacy_continuation_blocks_both_original_and_new_identity(setup_registry):
    _, _, registry, _ = setup_registry
    registry.windows["new-run"] = SimpleNamespace(
        request=SimpleNamespace(recovery_task_id=None, recovery_record={"task_id": "legacy"}),
        run=SimpleNamespace(state="running", shutdown=lambda: None),
    )
    assert registry.is_recovery_active("legacy")
    assert registry.is_recovery_active("new-run")
    assert not registry.request_recovery(record("legacy"))


@pytest.mark.parametrize("legacy", [False, True])
def test_launch_transfers_recovery_identity_and_directory(launch, tmp_path, monkeypatch, legacy):
    selected = {"task_id": "original", "mode": "translate", "overwrite": True, "legacy": legacy}
    launch.window._recovery_selection = (selected, False)
    launch.window._task_project_directory_provider = lambda: tmp_path
    monkeypatch.setattr(task_recovery_binding, "recovery_sources", lambda window, _: window._task_sources())
    task_runtime.start_task(launch.window)
    launch.wait(lambda: launch.window._task_launch is None)
    request = launch.progress[0].request
    assert request.recovery_directory == tmp_path
    assert request.recovery_record is selected
    assert request.recovery_task_id == (None if legacy else "original")
    assert request.spec.overwrite is (not legacy)


def test_translation_discovery_does_not_present_proofread_zero_as_total_progress(tmp_path):
    old, request, session, _ = setup_binding(tmp_path)
    old.release()
    request.run_id = "translation"
    task = session.tasks[0]
    session.tasks = (replace(task, translate_entries=task.polish_entries, polish_entries=()),)
    binding = TaskRecoveryBinding(request, session)
    records = {value["task_id"]: value for value in read_recovery_records(tmp_path)}
    assert "saved_count" not in records["translation"]
    assert "已保存候选 0 条" not in recovery_summary(records["translation"])
    binding.release()


def test_damaged_result_isolated_and_explained_in_history(setup_registry, monkeypatch):
    from tests.ui.tools.test_task_recovery_discovery import wait_until
    from transbridge.application.translation import task_result_recovery
    from transbridge.ui.tools.ai_translator.task_history_dialog import TaskHistoryDialog

    app, _, registry, store = setup_registry
    for task_id in ("damaged", "healthy"):
        store.create(record(task_id, state="partial"))

    def load(_store, value):
        if value["task_id"] == "damaged":
            raise ValueError("候选校验链损坏")
        return None

    monkeypatch.setattr(task_result_recovery, "load_recovery_results", load)
    dialog = TaskHistoryDialog(registry)
    registry.history_windows.append(dialog)
    wait_until(app, lambda: not dialog.busy and dialog.tasks.topLevelItemCount() == 2)
    from PyQt6.QtCore import Qt

    for index in range(2):
        row = dialog.tasks.topLevelItem(index)
        dialog.tasks.setCurrentItem(row)
        if row.data(0, Qt.ItemDataRole.UserRole) == "damaged":
            # Discovery only reads metadata; selected content is validated in a worker.
            assert dialog.continue_button.isEnabled()
            dialog.continue_button.click()
            wait_until(app, lambda: not registry._recovery_workers)
            dialog._populate()
            assert not dialog.continue_button.isEnabled()
            assert "候选校验链损坏" in dialog.status.text()
            assert "候选校验链损坏" in row.toolTip(2)
        else:
            assert dialog.continue_button.isEnabled()
            assert not dialog.status.text()
