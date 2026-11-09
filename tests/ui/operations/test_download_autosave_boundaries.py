"""Download autosave publishes exactly what reached disk across failure boundaries."""

import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.plugin_fixtures import write_plugin
from tests.ui.operations.test_batch_download_autosave import disk_snapshot
from tests.ui.operations.test_batch_download_session import case as batch_case_fixture, compare, states
from transbridge.application.projects import ProjectSourceRequest
from transbridge.application.tasks import JobState, OwnerRef
from transbridge.persistence.project_store.lifecycle import ProjectLifecycleStore

case = batch_case_fixture


def assert_saved_current(case):
    active = case.services.project_lifecycle.active
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    assert case.services.projects.read_snapshot(active.project_ref) == active.project
    assert not active.dirty


def test_sqlite_commit_succeeded_but_save_reply_failed_is_verified_and_published(case, monkeypatch):
    compare(case)
    original = ProjectLifecycleStore._save
    lost_reply = []

    def save_then_lose_reply(*args, **kwargs):
        result = original(*args, **kwargs)
        if not lost_reply:
            lost_reply.append(True)
            raise OSError("写入已完成，但完成回执丢失")
        return result

    monkeypatch.setattr(ProjectLifecycleStore, "_save", save_then_lose_reply)
    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert lost_reply == [True] and receipt.updated == 2
    assert_saved_current(case)
    assert not case.services.project_snapshots.list(case.context.runtime_context)
    assert {translation for translation, _stage in states(case).values()} == {"云端1", "云端2"}


def test_transient_verification_read_error_after_write_does_not_create_false_failure(case, monkeypatch):
    compare(case)
    original_save = ProjectLifecycleStore._save
    original_verify = ProjectLifecycleStore.verify_saved
    wrote, failed = [], []

    def save(*args, **kwargs):
        original_save(*args, **kwargs)
        wrote.append(True)
        raise OSError("写入已完成，但完成回执丢失")

    def verify(*args, **kwargs):
        if wrote and not failed:
            failed.append(True)
            raise OSError("SQLite 已提交，第一次后验读取暂时失败")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(ProjectLifecycleStore, "_save", save)
    monkeypatch.setattr(ProjectLifecycleStore, "verify_saved", verify)
    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert wrote == failed == [True]
    assert receipt.updated == 2
    assert_saved_current(case)


@pytest.mark.parametrize("late_edit", [None, "clear", "label", "regular-save"])
def test_committed_download_retry_rebases_working_copy_without_replaying_save(case, monkeypatch, late_edit):
    compare(case)
    task = case.session.task_results
    key = case.sources[0].entries[0].identity
    original_save = ProjectLifecycleStore._save
    original_verify = ProjectLifecycleStore.verify_saved
    writes, reads = [], []

    def lose_reply(store, save):
        original_save(store, save)
        writes.append(save.variant.revision)
        raise OSError("写入已完成，但完成回执丢失")

    def verify(store, save):
        reads.append(save.variant.revision)
        if len(reads) <= 2:
            raise OSError("后验读取暂时失败")
        return original_verify(store, save)

    with monkeypatch.context() as patch:
        patch.setattr(ProjectLifecycleStore, "_save", lose_reply)
        patch.setattr(ProjectLifecycleStore, "verify_saved", verify)
        with pytest.raises(RuntimeError, match="未确认成功"):
            case.session.apply(lambda *_: None, case.cancel)
        assert all(translation == "" for translation, _stage in states(case).values())
        assert {entry.translation for entry in disk_snapshot(case).entries} == {"云端1", "云端2"}
        (original,) = task.store.decisions.list(task.run_id)
        assert original.receipt is None
        if late_edit == "clear":
            result = case.context.project_commands.update_entry(
                key, case.context.runtime_context, translation="", stage=1
            )
            assert result.is_success, result.diagnostics
        elif late_edit == "label":
            result = case.context.project_commands.replace_labels(
                {key: {"later"}}, {"later": {"name": "Later"}}, case.context.runtime_context, only_listed=True
            )
            assert result.is_success, result.diagnostics
        elif late_edit == "regular-save":
            commands = case.context.project_commands
            context = case.context.runtime_context
            assert commands.update_entry(key, context, translation="Saved during recovery", stage=1).is_success
            patch.setattr(ProjectLifecycleStore, "_save", original_save)
            saved = commands.save(context)
            assert saved.is_success, saved.diagnostics
            assert_saved_current(case)
        calls = tuple(case.gateway.calls)

        receipt = case.session.apply(lambda *_: None, case.cancel)

    assert not receipt.requires_review
    (confirmed,) = task.store.decisions.list(task.run_id)
    assert confirmed.request.operation_id == original.request.operation_id
    assert len(confirmed.receipt["entries"]) == 2
    record = task.store.load(task.run_id)
    assert record["state"] == "completed"
    assert record["merge"] == {
        "applied_count": 2,
        "unchanged_count": 0,
        "pending_count": 0,
        "application_state": "committed",
    }
    assert tuple(case.gateway.calls) == calls
    assert writes == [1] and reads == [1, 1, 1]
    active = case.services.project_lifecycle.active
    assert active.persisted_variant_revision == (2 if late_edit == "regular-save" else 1)
    if late_edit in {"clear", "label"}:
        assert active.dirty
        assert states(case)[key] == (("", 1) if late_edit == "clear" else ("云端1", 3))
        assert states(case)[case.sources[1].entries[0].identity] == ("云端2", 3)
        saved = case.context.project_commands.save(case.context.runtime_context)
        assert saved.is_success, saved.diagnostics
    if late_edit == "label":
        assert next(entry for entry in disk_snapshot(case).entries if entry.entry_key == key).labels == ("later",)
    if late_edit == "regular-save":
        assert states(case)[key] == ("Saved during recovery", 1)
    assert_saved_current(case)


@pytest.mark.parametrize("change", ["add", "remove"])
def test_source_change_waits_for_pending_download_then_saves_current_edits(case, tmp_path, monkeypatch, change):
    compare(case)
    commands = case.context.project_commands
    context = case.context.runtime_context
    kept_key = case.sources[1].entries[0].identity
    added = write_plugin(tmp_path / "new.esp", [(0xC00, "Added", "New source")])
    real_save = ProjectLifecycleStore._save
    real_verify = ProjectLifecycleStore.verify_saved
    blocked = True
    writes = []

    def lose_reply(store, save):
        real_save(store, save)
        writes.append(save.variant.revision)
        raise OSError("lost committed reply")

    def verify(store, save):
        if blocked:
            raise OSError("verification unavailable")
        return real_verify(store, save)

    def mutate_sources():
        if change == "add":
            return commands.add_source(ProjectSourceRequest(str(added)), context)
        return commands.remove_source(case.sources[0].source_id, context)

    with monkeypatch.context() as patch:
        patch.setattr(ProjectLifecycleStore, "_save", lose_reply)
        patch.setattr(ProjectLifecycleStore, "verify_saved", verify)
        with pytest.raises(RuntimeError, match="未确认成功"):
            case.session.apply(lambda *_: None, case.cancel)
        assert commands.update_entry(kept_key, context, translation="Later manual edit", stage=1).is_success
        before = case.services.project_lifecycle.active
        rejected = mutate_sources()
        assert not rejected.is_success and rejected.diagnostics[0].code == "DURABLE_VARIANT_SAVE_CHECK_REQUIRED"
        assert case.services.project_lifecycle.active is before and before.dirty
        assert states(case)[kept_key] == ("Later manual edit", 1)
        blocked = False
        stale = mutate_sources()
        assert not stale.is_success and stale.diagnostics[0].code == "SOURCE_UPDATE_STALE"
        active = case.services.project_lifecycle.active
        assert active.persisted_variant_revision == 1 and active.dirty
        assert states(case)[kept_key] == ("Later manual edit", 1)

    changed = mutate_sources()
    assert changed.is_success, changed.diagnostics
    saved = commands.save(context)
    assert saved.is_success, saved.diagnostics
    assert writes == [1]
    assert len(states(case)) == (3 if change == "add" else 1)
    assert states(case)[kept_key] == ("Later manual edit", 1)
    assert_saved_current(case)


def test_source_change_after_unconfirmed_precommit_failure_uses_unchanged_base(case, monkeypatch):
    compare(case)
    before = disk_snapshot(case)
    task = case.session.task_results

    def fail(*_args):
        raise OSError("initial write or verification unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(ProjectLifecycleStore, "_save", fail)
        patch.setattr(ProjectLifecycleStore, "verify_saved", fail)
        with pytest.raises(RuntimeError, match="未确认成功"):
            case.session.apply(lambda *_: None, case.cancel)

    assert disk_snapshot(case) == before
    commands, context = case.context.project_commands, case.context.runtime_context
    removed = commands.remove_source(case.sources[0].source_id, context)
    assert removed.is_success, removed.diagnostics
    saved = commands.save(context)
    assert saved.is_success, saved.diagnostics
    assert len(states(case)) == 1
    (application,) = task.store.decisions.list(task.run_id)
    assert application.receipt is None and application.state.value == "prepared"
    assert_saved_current(case)


def test_real_disk_save_failure_preserves_unsaved_state_and_allows_in_place_retry(case, monkeypatch):
    key = case.sources[0].entries[0].identity
    assert case.context.project_commands.update_entry(
        key, case.context.runtime_context, translation="下载前未保存的编辑", stage=1
    ).is_success
    compare(case)
    before = states(case)
    disk_before = disk_snapshot(case)
    calls_before = tuple(case.gateway.calls)

    def fail_before_commit(*_args, **_kwargs):
        raise OSError("测试写盘故障：磁盘空间暂时不足")

    with monkeypatch.context() as patch:
        patch.setattr(ProjectLifecycleStore, "_save", fail_before_commit)
        with pytest.raises(RuntimeError, match="磁盘空间"):
            case.session.apply(lambda *_: None, case.cancel)

    assert not case.services.project_snapshots.list(case.context.runtime_context)
    assert states(case) == before and disk_snapshot(case) == disk_before
    assert case.services.project_lifecycle.active.dirty
    assert case.session.apply(lambda *_: None, case.cancel).updated == 2
    assert tuple(case.gateway.calls) == calls_before
    assert_saved_current(case)


@pytest.mark.parametrize("late_cancel", [False, True])
def test_task_cannot_complete_before_saved_candidate_is_also_current_in_memory(case, monkeypatch, late_cancel):
    compare(case)
    original_save = ProjectLifecycleStore._save
    original_complete = case.runtime.tasks.complete
    completed = []

    def save(*args, **kwargs):
        result = original_save(*args, **kwargs)
        if late_cancel:
            case.cancel.set()
        return result

    def complete(*args, **kwargs):
        # Both durable write and memory publication must precede the task's
        # terminal completion; otherwise a late cancel can report a false result.
        assert_saved_current(case)
        completed.append(True)
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(ProjectLifecycleStore, "_save", save)
    monkeypatch.setattr(case.runtime.tasks, "complete", complete)
    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert receipt.updated == 2 and completed
    actor = OwnerRef(
        case.context.runtime_context.owner_id,
        "gui.operation-plan",
        case.context.active_project_id,
        case.context.active_variant_id,
    )
    assert case.runtime.tasks.list(actor)[-1].state is JobState.COMPLETED
    assert_saved_current(case)


def test_version_switch_before_publication_does_not_save_download_into_either_version(case, monkeypatch):
    compare(case)
    active = case.services.project_lifecycle.active
    original_ref = active.formal_variant_ref
    original_disk = disk_snapshot(case, original_ref)
    task = case.session.task_results
    original_merge = case.context.project_commands.merge_entry_states
    switched = []

    def switch_before_merge(*args, **kwargs):
        changed = case.context.project_commands.create_variant(
            "下载提交前切换的新版本", case.context.runtime_context, copy_active=True
        )
        assert changed.is_success, changed.diagnostics
        switched.append(case.services.project_lifecycle.active.formal_variant_ref)
        return original_merge(*args, **kwargs)

    monkeypatch.setattr(case.context.project_commands, "merge_entry_states", switch_before_merge)
    with pytest.raises(RuntimeError):
        case.session.apply(lambda *_: None, case.cancel)

    current = case.services.project_lifecycle.active
    assert switched == [current.formal_variant_ref] and current.formal_variant_ref != original_ref
    assert disk_snapshot(case, original_ref) == original_disk
    assert {translation for translation, _stage in states(case).values()} == {""}
    assert case.services.variants.read_snapshot(current.formal_variant_ref) == current.variant.snapshot().to_dto()
    assert len(task.store.load_results(task.run_id)["rows"]) == 2
    assert not case.services.project_snapshots.list(case.context.runtime_context)
