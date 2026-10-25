"""Download success means the current local Variant is durable and clean."""

from pathlib import Path

import pytest

from tests.application.projects.test_categorized_upload import project as project
from tests.ui.operations.test_batch_download_session import case as _case_fixture, compare, states
from transbridge.persistence.v2 import VariantRepository
from transbridge.persistence.v2.variant import VariantSnapshot

case = _case_fixture


def disk_snapshot(case):
    active = case.services.project_lifecycle.active
    repository = VariantRepository(case.services.root, case.services.filesystem)
    return VariantSnapshot.from_dto(repository.read_snapshot(active.formal_variant_ref), active.formal_variant_ref)


def test_download_automatically_saves_current_version_and_retains_unsaved_recovery_point(case):
    key = case.sources[0].entries[0].identity
    assert case.context.project_commands.update_entry(
        key,
        case.context.runtime_context,
        translation="下载前未保存的编辑",
        stage=1,
    ).is_success
    before = case.services.project_lifecycle.active.variant.snapshot()
    assert case.services.project_lifecycle.active.dirty
    compare(case)

    receipt = case.session.apply(lambda *_: None, case.cancel)

    active = case.services.project_lifecycle.active
    assert receipt.updated == 2
    assert not active.dirty
    assert active.variant.revision == before.revision + 1
    assert disk_snapshot(case) == active.variant.snapshot()
    assert {entry.translation for entry in disk_snapshot(case).entries} == {"云端1", "云端2"}
    snapshots = case.services.project_snapshots.list(case.context.runtime_context)
    assert len(snapshots) == 1 and "批量下载前" in snapshots[0].name
    assert snapshots[0].revision == before.revision
    assert any("已更新并保存" in detail for detail in receipt.details)


def test_disk_failure_preserves_original_state_and_retries_same_download_and_recovery_point(case, monkeypatch):
    key = case.sources[0].entries[0].identity
    assert case.context.project_commands.update_entry(
        key,
        case.context.runtime_context,
        translation="不能丢的未保存编辑",
        stage=1,
    ).is_success
    compare(case)
    active = case.services.project_lifecycle.active
    before = active.variant.snapshot()
    persisted_before = disk_snapshot(case)
    projection_before = case.services.project_projection.snapshot()
    variant_path = Path(case.services.variants.path_for(active.formal_variant_ref))
    original_replace = case.services.filesystem.replace
    writes = []

    def fail_version(source, destination):
        if Path(destination) == variant_path:
            writes.append(destination)
            raise PermissionError("测试：当前版本文件暂时被占用")
        return original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(case.services.filesystem, "replace", fail_version)
        with pytest.raises(RuntimeError, match="自动保存|保存失败"):
            case.session.apply(lambda *_: None, case.cancel)

    assert writes
    assert case.services.project_lifecycle.active.variant.snapshot() == before
    assert case.services.project_lifecycle.active.dirty
    assert disk_snapshot(case) == persisted_before
    assert case.services.project_projection.snapshot() == projection_before
    snapshots_before_retry = case.services.project_snapshots.list(case.context.runtime_context)
    assert len(snapshots_before_retry) == 1
    reads_before_retry = tuple(case.gateway.calls)

    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert receipt.updated == 2
    assert not case.services.project_lifecycle.active.dirty
    assert disk_snapshot(case) == case.services.project_lifecycle.active.variant.snapshot()
    assert tuple(case.gateway.calls) == reads_before_retry
    assert case.services.project_snapshots.list(case.context.runtime_context) == snapshots_before_retry


def test_autosave_preserves_and_saves_unselected_edits_in_same_version(case):
    chosen, other = case.sources
    key = other.entries[0].identity
    compare(case, (chosen.source_id,))
    case.context.project_commands.update_entry(
        key, case.context.runtime_context, translation="另一插件的新编辑", stage=1
    )

    assert case.session.apply(lambda *_: None, case.cancel).updated == 1

    assert states(case)[key] == ("另一插件的新编辑", 1)
    saved = {entry.entry_key: entry for entry in disk_snapshot(case).entries}
    assert saved[key].translation == "另一插件的新编辑"
    assert not case.services.project_lifecycle.active.dirty


def test_cancel_before_durable_write_leaves_disk_unchanged(case, monkeypatch):
    compare(case)
    before = disk_snapshot(case)
    original = case.context.project_commands.save_snapshot

    def cancel_after_snapshot(*args):
        result = original(*args)
        case.cancel.set()
        return result

    monkeypatch.setattr(case.context.project_commands, "save_snapshot", cancel_after_snapshot)
    with pytest.raises(RuntimeError):
        case.session.apply(lambda *_: None, case.cancel)
    assert disk_snapshot(case) == before
    assert len(case.services.project_snapshots.list(case.context.runtime_context)) == 1


def test_matching_concurrent_edit_is_still_automatically_saved_on_success(case):
    compare(case)
    for index, source in enumerate(case.sources, 1):
        case.context.project_commands.update_entry(
            source.entries[0].identity,
            case.context.runtime_context,
            translation=f"云端{index}",
            stage=3,
        )
    assert case.services.project_lifecycle.active.dirty

    receipt = case.session.apply(lambda *_: None, case.cancel)

    assert receipt.updated == 0 and not receipt.requires_review
    assert not case.services.project_lifecycle.active.dirty
    assert disk_snapshot(case) == case.services.project_lifecycle.active.variant.snapshot()
