from dataclasses import replace
from itertools import count

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.identity import ExternalEntryRef
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.application.projects.categorized_upload import ProjectCategorizedUploadService
from transbridge.application.projects.variant_commands import EntryStatePatch
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer


@pytest.fixture
def project(tmp_path, request):
    first = write_plugin(
        tmp_path / "主体.esp",
        [
            (0x800 + index, f"Shared{index}" if index else "Shared", "Alice")
            for index in range(getattr(request, "param", 1))
        ],
    )
    second = write_plugin(tmp_path / "扩展.esp", [(0x800, "Shared", "Bob")])
    ids = count()

    def next_id():
        return f"upload-{next(ids)}"

    services = build_persistence_v2_services(
        tmp_path / "data", id_factory=next_id, timestamp_factory=lambda: "2026-10-23T00:00:00+00:00"
    )
    context = RequestContext("upload-test", run_id="upload")
    created = services.gui_project_commands.create_project(
        ProjectProvisioningRequest(
            "分类上传测试",
            source=ProjectSourceRequest(
                str(first), FormatId.PLUGIN_SSE, options=(("source_namespace", "source:plugin:main"),)
            ),
            additional_sources=(
                ProjectSourceRequest(
                    str(second), FormatId.PLUGIN_SSE, options=(("source_namespace", "source:plugin:expansion"),)
                ),
            ),
        ),
        context,
    )
    assert created.is_success, created.diagnostics
    service = ProjectCategorizedUploadService(
        services.project_lifecycle, services.baselines, TranslationIoProjectSourcePreparer()
    )
    yield services, service, context, first, second
    services.close()


def _snapshot(project, *, endpoint="https://paratranz.cn/api", remote_project_id=42):
    _services, service, context, *_ = project
    result = service.snapshot(context, endpoint=endpoint, remote_project_id=remote_project_id)
    assert result.is_success, result.diagnostics
    return result.value


def test_snapshot_contains_all_registered_sources_and_authoritative_unsaved_translations(project):
    services, service, context, *_ = project
    original = _snapshot(project)
    source = original.sources[0]
    key = source.entries[0].identity
    assert services.gui_project_commands.update_entry(key, context, translation="尚未保存", stage=3).is_success
    assert services.project_lifecycle.active.dirty

    snapshot = _snapshot(project)

    assert {source.label for source in snapshot.sources} == {"主体.esp", "扩展.esp"}
    assert len({source.source_id for source in snapshot.sources}) == 2
    assert len({source.entries[0].identity for source in snapshot.sources}) == 2
    state = next(entry for source in snapshot.sources for entry in source.entries if entry.identity == key)
    assert (state.translation, state.stage) == ("尚未保存", 3)
    assert service.current(original, context).diagnostics[0].code == "CATEGORIZED_UPLOAD_STALE"


def test_snapshot_excludes_disabled_source_and_uses_source_id_after_rename(project):
    services, _service, context, *_ = project
    before = _snapshot(project)
    active = services.project_lifecycle.active
    data = dict(active.project.envelope.data)
    sources = [dict(source) for source in data["sources"]]
    first_id = before.sources[0].source_id
    for source in sources:
        if source["source_id"] == first_id:
            source["display_name"] = "新插件名.esp"
        else:
            source["enabled"] = False
    data["sources"] = sources
    updated = replace(active.project, envelope=replace(active.project.envelope, data=data, revision=1))
    assert services.project_lifecycle.commit_project_update(updated, 0, context).is_success

    snapshot = _snapshot(project)

    assert len(snapshot.sources) == 1
    assert (snapshot.sources[0].source_id, snapshot.sources[0].label) == (first_id, "新插件名.esp")


def test_changed_source_file_requires_refresh_instead_of_silent_reclassification(project):
    _services, service, context, first, _second = project
    write_plugin(first, [(0x800, "Shared", "Changed on disk")])

    result = service.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=42)

    assert not result.is_success
    assert result.diagnostics[0].code == "PROJECT_SOURCE_FINGERPRINT_MISMATCH"


@pytest.mark.parametrize("change", ["project", "variant", "generation", "project_revision", "variant_revision"])
def test_current_rejects_snapshot_with_changed_identity_or_revision(project, change):
    _services, service, context, *_ = project
    snapshot = _snapshot(project)
    invalid = replace(
        snapshot,
        **{
            "project": {"project_id": "other"},
            "variant": {"variant_id": "other"},
            "generation": {"generation": snapshot.generation + 1},
            "project_revision": {"project_revision": snapshot.project_revision + 1},
            "variant_revision": {"variant_revision": snapshot.variant_revision + 1},
        }[change],
    )

    result = service.current(invalid, context)

    assert not result.is_success
    assert result.diagnostics[0].code == "CATEGORIZED_UPLOAD_STALE"


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"project_id": "other"}, "PROJECT_CONTEXT_MISMATCH"),
        ({"variant_id": "other"}, "VARIANT_CONTEXT_MISMATCH"),
    ],
)
def test_snapshot_rejects_another_local_project_or_variant_context(project, changes, code):
    _services, service, context, *_ = project
    result = service.snapshot(replace(context, **changes), endpoint="https://paratranz.cn", remote_project_id=42)
    assert not result.is_success
    assert result.diagnostics[0].code == code


def test_current_checks_local_revisions_without_rehydrating_source_files(project):
    services, _service, context, first, second = project
    captured = _snapshot(project)
    first.unlink()
    second.unlink()

    result = services.gui_project_commands.categorized_upload_current(captured, context)

    assert result.is_success
    assert result.value is captured


@pytest.mark.parametrize("old_metadata", [None, "malformed", [{"endpoint": "https://paratranz.cn", "files": "broken"}]])
def test_malformed_historical_binding_metadata_is_not_read_or_rewritten(project, old_metadata):
    services, _service, context, *_ = project
    active = services.project_lifecycle.active
    data = dict(active.project.envelope.data)
    data["paratranz_categorized_uploads"] = old_metadata
    updated = replace(
        active.project,
        envelope=replace(active.project.envelope, data=data, revision=active.project.envelope.revision + 1),
    )
    assert services.project_lifecycle.commit_project_update(
        updated, active.project.envelope.revision, context
    ).is_success
    current = services.project_lifecycle.active
    before = (current.project, current.variant.snapshot(), services.project_lifecycle.generation, current.dirty)

    captured = services.gui_project_commands.categorized_upload_snapshot(
        context, endpoint="https://paratranz.cn/api", remote_project_id=42
    )

    assert captured.is_success, captured.diagnostics
    assert len(captured.value.sources) == 2
    after = services.project_lifecycle.active
    assert (after.project, after.variant.snapshot(), services.project_lifecycle.generation, after.dirty) == before
    assert after.project.envelope.data["paratranz_categorized_uploads"] == old_metadata


def test_source_snapshot_omits_remote_references_without_changing_local_state_or_saving(project):
    services, service, context, *_ = project
    original = _snapshot(project)
    key = original.sources[0].entries[0].identity
    reference = ExternalEntryRef("paratranz", "project:42", 101, (("endpoint", "malformed"), ("file_id", 11)))
    assert services.gui_project_commands.replace_entry_records(
        {key: EntryStatePatch("未保存译文", 3, (reference,))}, context
    ).is_success
    active = services.project_lifecycle.active
    before = (active.project, active.variant.snapshot(), services.project_lifecycle.generation, active.dirty)
    disk_project = services.projects.read_snapshot(active.project_ref)
    disk_variant = services.variants.read_snapshot(active.formal_variant_ref)

    captured = _snapshot(project)

    assert all(not entry.external_refs for source in captured.sources for entry in source.entries)
    state = next(entry for entry in active.variant.snapshot().entries if entry.entry_key == key)
    assert state.external_refs == (reference,)
    assert state.translation == "未保存译文"
    assert (active.project, active.variant.snapshot(), services.project_lifecycle.generation, active.dirty) == before
    assert services.projects.read_snapshot(active.project_ref) == disk_project
    assert services.variants.read_snapshot(active.formal_variant_ref) == disk_variant
    assert service.current(captured, context).is_success


def test_source_update_and_reopen_preserve_stable_source_identity_without_upload_bindings(project):
    services, _service, context, first, _second = project
    original = _snapshot(project)
    source = next(source for source in original.sources if source.label == first.name)
    active = services.project_lifecycle.active
    project_path = services.projects.path_for(active.project_ref)
    replacement = first.with_name("新版主体.esp")
    replacement.write_bytes(first.read_bytes())
    prepared = services.project_source_updates.prepare(project_path, source.source_id, str(replacement), context)
    assert prepared.is_success, prepared.diagnostics
    updated = services.project_source_updates.commit(prepared.value.token, context)
    assert updated.is_success, updated.diagnostics
    reopened = services.current_project_opener.open_path(project_path, context)
    assert reopened.is_success, reopened.diagnostics

    captured = _snapshot(project)

    renamed = next(item for item in captured.sources if item.source_id == source.source_id)
    assert renamed.label == replacement.name
    assert renamed.entries[0].identity == source.entries[0].identity
    assert "paratranz_categorized_uploads" not in services.project_lifecycle.active.project.envelope.data


@pytest.mark.parametrize("project_id", [0, -1, True])
def test_source_snapshot_rejects_invalid_remote_target(project, project_id):
    _services, service, context, *_ = project
    result = service.snapshot(context, endpoint="https://paratranz.cn", remote_project_id=project_id)
    assert not result.is_success
