from dataclasses import replace
import errno

import pytest

from tests.application.projects.test_categorized_upload import project as project_fixture
from transbridge.application.projects import EntryStatePatch
from transbridge.application.tasks import TaskCancelled
from transbridge.persistence.v2.ids import VariantId, VariantRef
from transbridge.persistence.v2.variant import VariantSnapshot

project = project_fixture


def sources(project):
    _services, service, context, *_ = project
    result = service.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42)
    assert result.is_success, result.diagnostics
    return result.value.sources


def save(project, patches, **options):
    services, _service, context, *_ = project
    active = services.project_lifecycle.active
    return services.gui_project_commands.replace_entry_records(
        patches,
        context,
        expected_project_revision=active.project.envelope.revision,
        expected_variant_revision=active.variant.revision,
        expected_variant_ref=active.formal_variant_ref,
        persist=True,
        **options,
    )


def disk_state(services, ref):
    return VariantSnapshot.from_dto(services.variants.read_snapshot(ref), ref)


def test_download_and_existing_unsaved_edits_are_durable_and_reopen_clean(project):
    services, _service, context, *_ = project
    first, second = sources(project)
    original = services.project_lifecycle.active
    first_key, second_key = first.entries[0].identity, second.entries[0].identity
    assert services.gui_project_commands.update_entry(
        first_key, context, translation="Prior unsaved edit", stage=3
    ).is_success
    assert services.project_lifecycle.active.dirty
    assert services.gui_project_commands.save_snapshot("before-download", context).is_success

    result = save(project, {second_key: EntryStatePatch("Downloaded translation", 5, ())})

    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    assert not active.dirty and not services.project_projection.snapshot().dirty
    persisted = disk_state(services, active.formal_variant_ref)
    assert persisted == active.variant.snapshot()
    assert {entry.entry_key: (entry.translation, entry.stage.value) for entry in persisted.entries} == {
        first_key: ("Prior unsaved edit", 3),
        second_key: ("Downloaded translation", 5),
    }
    assert services.projects.read_snapshot(active.project_ref) == active.project
    assert any(item.name == "before-download" for item in services.project_snapshots.list(context))
    reopened = services.current_project_opener.open_path(services.projects.path_for(original.project_ref), context)
    assert reopened.is_success, reopened.diagnostics
    assert not services.project_lifecycle.active.dirty
    assert services.project_lifecycle.active.variant.snapshot() == persisted


def test_durable_guard_covers_disk_and_memory_exchange_and_publishes_projection_once(project):
    services, _service, _context, *_ = project
    source = sources(project)[0]
    before = services.project_lifecycle.active
    before_variant = before.variant.snapshot()
    notifications, guards = [], []
    subscription = services.project_projection.subscribe(notifications.append, replay=False)

    def guard(mutation):
        guards.append("before")
        assert services.project_lifecycle.active.variant.snapshot() == before_variant
        assert disk_state(services, before.formal_variant_ref) == before_variant
        mutation()
        after = services.project_lifecycle.active
        assert after.variant.snapshot() == disk_state(services, after.formal_variant_ref)
        assert after.variant.revision == before_variant.revision + 1
        assert not after.dirty
        assert notifications == []
        guards.append("after")

    result = save(project, {source.entries[0].identity: EntryStatePatch("Downloaded", 3, ())}, commit_mutation=guard)

    assert result.is_success, result.diagnostics
    assert guards == ["before", "after"]
    assert len(notifications) == 1 and not notifications[0].dirty
    subscription.close()


@pytest.mark.parametrize("error", [PermissionError("locked"), OSError(errno.ENOSPC, "disk is full")])
def test_write_failure_preserves_memory_disk_projection_and_retries_without_reopening(project, monkeypatch, error):
    services, _service, context, *_ = project
    first, second = sources(project)
    assert services.gui_project_commands.update_entry(
        first.entries[0].identity, context, translation="Unsaved work", stage=3
    ).is_success
    before = services.project_lifecycle.active
    memory_before = before.variant.snapshot()
    disk_before = services.variants.read_snapshot(before.formal_variant_ref)
    project_before = services.projects.read_snapshot(before.project_ref)
    projection_before = services.project_projection.snapshot()
    destination = services.variants.path_for(before.formal_variant_ref)
    original_replace = services.filesystem.replace
    patch = {second.entries[0].identity: EntryStatePatch("Downloaded", 5, ())}

    def fail_variant(source, target):
        if target == destination:
            raise error
        return original_replace(source, target)

    with monkeypatch.context() as injected:
        injected.setattr(services.filesystem, "replace", fail_variant)
        failed = save(project, patch)

    assert not failed.is_success
    assert failed.diagnostics[0].code == "DURABLE_VARIANT_SAVE_FAILED"
    assert "当前窗口重试" in failed.diagnostics[0].message
    assert ("权限" if isinstance(error, PermissionError) else "磁盘空间不足") in failed.diagnostics[0].message
    assert services.project_lifecycle.active is before
    assert before.variant.snapshot() == memory_before and before.dirty
    assert services.variants.read_snapshot(before.formal_variant_ref) == disk_before
    assert services.projects.read_snapshot(before.project_ref) == project_before
    assert services.project_projection.snapshot() == projection_before
    retried = save(project, patch)
    assert retried.is_success, retried.diagnostics
    assert not services.project_lifecycle.active.dirty
    assert disk_state(services, before.formal_variant_ref) == services.project_lifecycle.active.variant.snapshot()


@pytest.mark.parametrize("failure", ["write_response", "verification"])
def test_completed_write_with_lost_response_is_verified_and_published_as_success(project, monkeypatch, failure):
    services, _service, _context, *_ = project
    source = sources(project)[0]
    calls = []
    if failure == "write_response":
        original = services.variants.save_if_revision

        def lost_response(*args, **kwargs):
            original(*args, **kwargs)
            calls.append("persisted")
            raise OSError("Lost acknowledgement after atomic replace")

        monkeypatch.setattr(services.variants, "save_if_revision", lost_response)
    else:
        from transbridge.persistence.v2 import project_save_journal

        def failed_verification(*args, **kwargs):
            calls.append("verification")
            raise OSError("Readback failed after atomic replace")

        monkeypatch.setattr(project_save_journal, "_verify_saved_record", failed_verification)

    result = save(project, {source.entries[0].identity: EntryStatePatch("Already on disk", 3, ())})

    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    assert calls == ["persisted" if failure == "write_response" else "verification"]
    assert not active.dirty
    assert disk_state(services, active.formal_variant_ref) == active.variant.snapshot()


def test_uncertain_completed_write_can_be_verified_on_retry_without_replaying_the_write(project, monkeypatch):
    services, _service, _context, *_ = project
    source = sources(project)[0]
    before = services.project_lifecycle.active
    real_save = services.variants.save_if_revision
    real_read = services.variants.read_snapshot
    writes, reads = [], []

    def lose_response(*args, **kwargs):
        result = real_save(*args, **kwargs)
        writes.append(result)
        raise OSError("Save acknowledgement lost")

    def temporarily_unreadable(ref):
        reads.append(ref)
        if len(reads) == 1:
            raise OSError("Readback temporarily unavailable")
        return real_read(ref)

    monkeypatch.setattr(services.variants, "save_if_revision", lose_response)
    monkeypatch.setattr(services.variants, "read_snapshot", temporarily_unreadable)
    patches = {source.entries[0].identity: EntryStatePatch("Verified after retry", 3, ())}
    first = save(project, patches)

    assert not first.is_success
    assert "未确认成功" in first.diagnostics[0].message
    assert services.project_lifecycle.active is before
    retried = save(project, patches)

    assert retried.is_success, retried.diagnostics
    assert len(writes) == 1
    after = services.project_lifecycle.active
    assert not after.dirty
    assert disk_state(services, after.formal_variant_ref) == after.variant.snapshot()


def test_persist_with_no_new_patch_saves_existing_dirty_work(project):
    services, _service, context, *_ = project
    source = sources(project)[0]
    key = source.entries[0].identity
    assert services.gui_project_commands.update_entry(key, context, translation="Unsaved only", stage=3).is_success
    before = services.project_lifecycle.active.variant.snapshot()

    result = save(project, {})

    assert result.is_success, result.diagnostics
    active = services.project_lifecycle.active
    assert not active.dirty
    assert disk_state(services, active.formal_variant_ref) == active.variant.snapshot()
    assert active.variant.snapshot().entries == before.entries


@pytest.mark.parametrize("fail_project_write", [False, True])
def test_both_pending_project_content_and_download_are_saved_as_one_recoverable_unit(
    project, monkeypatch, fail_project_write
):
    services, _service, context, *_ = project
    source = sources(project)[0]
    active = services.project_lifecycle.active
    data = dict(active.project.envelope.data)
    data["sources"] = [dict(record) for record in data["sources"]]
    data["sources"][0]["display_name"] = "Renamed local source.esp"
    changed_project = replace(
        active.project,
        envelope=replace(active.project.envelope, revision=active.project.envelope.revision + 1, data=data),
    )
    changed = services.project_lifecycle.commit_active_content(
        changed_project,
        active.variant.snapshot(),
        context,
        expected_project_revision=active.project.envelope.revision,
        expected_variant_revision=active.variant.revision,
    )
    assert changed.is_success, changed.diagnostics
    before = services.project_lifecycle.active
    project_before = services.projects.read_snapshot(before.project_ref)
    variant_before = services.variants.read_snapshot(before.formal_variant_ref)
    patch = {source.entries[0].identity: EntryStatePatch("Downloaded with source metadata", 3, ())}

    if fail_project_write:
        original = services.filesystem.replace
        project_path = services.projects.path_for(before.project_ref)

        def fail_project(source, destination):
            if destination == project_path:
                raise PermissionError("Project file is temporarily locked")
            return original(source, destination)

        with monkeypatch.context() as injected:
            injected.setattr(services.filesystem, "replace", fail_project)
            failed = save(project, patch)
        assert not failed.is_success
        assert services.project_lifecycle.active is before
        assert services.projects.read_snapshot(before.project_ref) == project_before
        assert services.variants.read_snapshot(before.formal_variant_ref) == variant_before

    result = save(project, patch)

    assert result.is_success, result.diagnostics
    after = services.project_lifecycle.active
    assert after.project == changed_project
    assert services.projects.read_snapshot(after.project_ref) == changed_project
    assert not after.dirty
    assert disk_state(services, after.formal_variant_ref) == after.variant.snapshot()


@pytest.mark.parametrize("guard", ["cancel", "ignored"])
def test_cancel_or_unexecuted_guard_cannot_write_or_publish_any_source(project, guard):
    services, _service, _context, *_ = project
    source = sources(project)[0]
    before = services.project_lifecycle.active
    disk_before = services.variants.read_snapshot(before.formal_variant_ref)
    projection_before = services.project_projection.snapshot()

    def mutation_guard(_mutation):
        if guard == "cancel":
            raise TaskCancelled("Cancelled before save")

    result = save(
        project,
        {source.entries[0].identity: EntryStatePatch("Must not apply", 5, ())},
        commit_mutation=mutation_guard,
    )

    assert not result.is_success
    assert services.project_lifecycle.active is before
    assert services.variants.read_snapshot(before.formal_variant_ref) == disk_before
    assert services.project_projection.snapshot() == projection_before


@pytest.mark.parametrize("change", ["ref", "revision", "context"])
def test_stale_or_wrong_variant_cannot_persist(project, change):
    services, _service, context, *_ = project
    source = sources(project)[0]
    before = services.project_lifecycle.active
    disk_before = services.variants.read_snapshot(before.formal_variant_ref)
    options = {
        "expected_project_revision": before.project.envelope.revision,
        "expected_variant_revision": before.variant.revision,
        "expected_variant_ref": before.formal_variant_ref,
    }
    if change == "ref":
        options["expected_variant_ref"] = VariantRef(VariantId("other"), before.project_ref.identity)
    elif change == "revision":
        options["expected_variant_revision"] += 1
    else:
        context = replace(context, variant_id="other")

    result = services.gui_project_commands.replace_entry_records(
        {source.entries[0].identity: EntryStatePatch("Wrong target", 3, ())}, context, persist=True, **options
    )

    assert not result.is_success
    assert services.project_lifecycle.active is before
    assert services.variants.read_snapshot(before.formal_variant_ref) == disk_before


def test_missing_durable_service_is_explicit_and_never_falls_back_to_memory_only(project):
    services, _service, context, *_ = project
    source = sources(project)[0]
    before = services.project_lifecycle.active.variant.snapshot()
    services.gui_project_commands._durable_variant_commits = None

    result = services.gui_project_commands.replace_entry_records(
        {source.entries[0].identity: EntryStatePatch("Not persisted", 3, ())}, context, persist=True
    )

    assert not result.is_success
    assert result.diagnostics[0].code == "DURABLE_VARIANT_SAVE_UNAVAILABLE"
    assert services.project_lifecycle.active.variant.snapshot() == before
