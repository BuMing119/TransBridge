"""Download autosave publishes exactly what reached disk across failure boundaries."""

from pathlib import Path

import pytest

from tests.application.projects.test_categorized_upload import project as project  # noqa: F401
from tests.ui.operations.test_batch_download_session import case as batch_case_fixture, compare, states
from transbridge.application.tasks import JobState, OwnerRef

case = batch_case_fixture


def assert_saved_current(case):
    active = case.services.project_lifecycle.active
    assert case.services.variants.read_snapshot(active.formal_variant_ref) == active.variant.snapshot().to_dto()
    assert case.services.projects.read_snapshot(active.project_ref) == active.project
    assert not active.dirty


def restore_points(case):
    directory = case.session.result_store.directory(case.session.record["result_id"])
    return tuple(sorted(directory.glob("merge-*.json")))


def test_variant_replace_succeeded_but_save_reply_failed_is_verified_and_published(case, monkeypatch):
    compare(case)
    repository = case.services.variants
    original = repository.save_if_revision
    lost_reply = []

    def save_then_lose_reply(*args, **kwargs):
        result = original(*args, **kwargs)
        if not lost_reply:
            lost_reply.append(True)
            raise OSError("写入已完成，但完成回执丢失")
        return result

    monkeypatch.setattr(repository, "save_if_revision", save_then_lose_reply)
    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert lost_reply == [True] and receipt.updated == 2
    assert_saved_current(case)
    assert restore_points(case)
    assert {translation for translation, _stage in states(case).values()} == {"云端1", "云端2"}


def test_transient_verification_read_error_after_write_does_not_create_false_failure(case, monkeypatch):
    compare(case)
    repository = case.services.variants
    original_save, original_load = repository.save_if_revision, repository.load
    wrote, failed = [], []

    def save(*args, **kwargs):
        result = original_save(*args, **kwargs)
        wrote.append(True)
        return result

    def verify(*args, **kwargs):
        if wrote and not failed:
            failed.append(True)
            raise OSError("文件已替换，第一次后验读取暂时失败")
        return original_load(*args, **kwargs)

    monkeypatch.setattr(repository, "save_if_revision", save)
    monkeypatch.setattr(repository, "load", verify)
    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert wrote == failed == [True]
    assert receipt.updated == 2
    assert_saved_current(case)


def test_real_disk_save_failure_preserves_unsaved_state_restore_point_and_in_place_retry(case, monkeypatch):
    key = case.sources[0].entries[0].identity
    assert case.context.project_commands.update_entry(
        key, case.context.runtime_context, translation="下载前未保存的编辑", stage=1
    ).is_success
    compare(case)
    before = states(case)
    active = case.services.project_lifecycle.active
    variant_path = Path(case.services.variants.path_for(active.formal_variant_ref))
    disk_before = variant_path.read_bytes()
    calls_before = tuple(case.gateway.calls)

    def fail_before_replacing(*_args, **_kwargs):
        raise OSError("测试写盘故障：磁盘空间暂时不足")

    with monkeypatch.context() as patch:
        patch.setattr(case.services.variants, "save_if_revision", fail_before_replacing)
        with pytest.raises(RuntimeError, match="磁盘空间"):
            case.session.apply(lambda *_: None, case.cancel)

    retained = {path: path.read_bytes() for path in restore_points(case)}
    assert retained
    assert states(case) == before and variant_path.read_bytes() == disk_before
    assert case.services.project_lifecycle.active.dirty
    assert case.session.apply(lambda *_: None, case.cancel).updated == 2
    assert tuple(case.gateway.calls) == calls_before
    assert all(path.read_bytes() == payload for path, payload in retained.items())
    assert_saved_current(case)


@pytest.mark.parametrize("late_cancel", [False, True])
def test_task_cannot_complete_before_saved_candidate_is_also_current_in_memory(case, monkeypatch, late_cancel):
    compare(case)
    original_save = case.services.variants.save_if_revision
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

    monkeypatch.setattr(case.services.variants, "save_if_revision", save)
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
    original_path = Path(case.services.variants.path_for(original_ref))
    original_disk = original_path.read_bytes()
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
    assert original_path.read_bytes() == original_disk
    assert {translation for translation, _stage in states(case).values()} == {""}
    assert case.services.variants.read_snapshot(current.formal_variant_ref) == current.variant.snapshot().to_dto()
    assert not case.session.record.get("merges")
    assert not list((Path(case.services.root) / "snapshots").glob("*.json"))
