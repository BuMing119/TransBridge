from pathlib import Path

import pytest

from tests.application.projects.test_source_update import _xml, project  # noqa: F401 - reuse project fixture
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectSourceRequest
from transbridge.persistence.project_store import source_updates
from transbridge.persistence.project_store.history import ProjectHistory


@pytest.fixture
def batch(project):  # noqa: F811 - pytest injects the imported fixture
    services, service, context, first, project_path, first_id = project
    second = first.with_name("second.xml")
    _xml(second, [(8, "Eight"), (9, "Nine")])
    added = services.gui_project_commands.add_source(ProjectSourceRequest(str(second), FormatId.XML_EET), context)
    assert added.is_success, added.diagnostics
    active = services.project_lifecycle.active
    for entry in active.variant.snapshot().entries:
        if not entry.translation:
            assert services.gui_project_commands.update_entry(
                entry.entry_key, context, translation="第二来源译文", stage=5
            ).is_success
    assert services.gui_project_commands.save(context).is_success
    assert services.gui_project_commands.create_variant("第二版", context, copy_active=True).is_success

    first_new = first.with_name("new-first.xml")
    second_new = first.with_name("new-second.xml")
    _xml(first_new, [(1, "One changed"), (2, "Two")])
    _xml(second_new, [(8, "Eight"), (9, "Nine changed"), (10, "Ten")])
    pairs = ((first_id, str(first_new)), (added.value.source_id, str(second_new)))
    return services, service, context, project_path, active.project_ref, pairs


def test_batch_migrates_every_version_and_creates_one_backup_group(batch):
    services, service, context, path, ref, pairs = batch
    prepared = service.prepare_batch(path, pairs, context)
    assert prepared.is_success, prepared.diagnostics
    preview = prepared.value
    assert preview.variant_count == 2
    first, second = preview.differences
    assert (first.changed, first.removed, second.changed, second.added) == (1, 1, 1, 1)
    assert (first.replacement_path, second.replacement_path) == tuple(path for _, path in pairs)

    previous = service._store.read(ref)
    targets_before = service.list_sources(path, context).value
    updated = service.commit(preview.token, context)
    assert updated.is_success, updated.diagnostics
    current = service._store.read(ref)
    assert current.project.envelope.revision == previous.project.envelope.revision + 1
    history = ProjectHistory(services.projects)
    backup_id = updated.value.backup_path.removeprefix("project-snapshot:")
    for index, (before, after) in enumerate(zip(previous.variants, current.variants, strict=True)):
        assert after.revision == before.revision + 1
        assert sorted((entry.translation, entry.stage.value) for entry in after.entries if entry.translation) == [
            ("第二来源译文", 2),
            ("第二来源译文", 5),
            ("译文", 2),
            ("译文", 5),
        ]
        assert len(after.entries) == 5
        (backup,) = history.list(after.ref)
        assert backup.identity == f"{backup_id}-{index}"
    targets = service.list_sources(path, context).value
    assert tuple(target.source_id for target in targets) == tuple(target.source_id for target in targets_before)
    assert {target.source_id: target.location for target in targets} == dict(pairs)
    reopened = services.current_project_opener.open_path(path, context)
    assert reopened.is_success, reopened.diagnostics
    assert len(reopened.value["hydrations"]) == 2


@pytest.mark.parametrize("failure", ["stale_input", "second_variant_write"])
def test_batch_leaves_all_sources_and_backups_unchanged_on_failure(batch, failure, monkeypatch):
    services, service, context, path, ref, pairs = batch
    expected = service._store.read(ref)
    sources_before = service.list_sources(path, context).value
    prepared = service.prepare_batch(path, pairs, context)
    assert prepared.is_success, prepared.diagnostics
    if failure == "stale_input":
        _xml(Path(pairs[1][1]), [(8, "Changed after the preview")])
    else:
        write_variant = source_updates.write_variant

        def fail_second(connection, variant, *args):
            if variant.ref == expected.variants[1].ref:
                raise RuntimeError("failure writing second version")
            return write_variant(connection, variant, *args)

        monkeypatch.setattr(source_updates, "write_variant", fail_second)

    failed = service.commit(prepared.value.token, context)
    assert not failed.is_success
    if failure == "stale_input":
        assert pairs[1][1] in failed.diagnostics[0].message
    assert not service.commit(prepared.value.token, context).is_success
    assert service._store.read(ref) == expected
    assert service.list_sources(path, context).value == sources_before
    history = ProjectHistory(services.projects)
    assert all(not history.list(variant.ref) for variant in expected.variants)


def test_batch_rejects_invalid_second_file_without_changing_project(batch):
    _services, service, context, path, ref, pairs = batch
    expected = service._store.read(ref)
    invalid = Path(pairs[1][1])
    invalid.write_bytes(b"invalid input")
    failed = service.prepare_batch(path, pairs, context)
    assert not failed.is_success
    assert str(invalid) in failed.diagnostics[0].message
    assert service._store.read(ref) == expected
