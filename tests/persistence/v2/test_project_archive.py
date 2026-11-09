from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, BrokenBarrierError
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.project_store.format import ProjectStorageConflict, UnsupportedProjectFormat
from transbridge.persistence.project_store.history import ProjectHistory
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import VariantRef


@pytest.fixture
def services_factory(tmp_path):
    opened = []

    def build(name, *, filesystem=None):
        services = build_persistence_v2_services(
            tmp_path / name,
            id_factory=lambda: uuid4().hex,
            timestamp_factory=lambda: "2026-08-31T00:00:00+08:00",
            filesystem=filesystem,
        )
        opened.append(services)
        return services

    yield build
    for services in reversed(opened):
        services.close()


def _ok(result):
    assert result.is_success, result.diagnostics
    return result.value


def _file_state(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in Path(root).rglob("*") if path.is_file()}


@pytest.fixture
def exported_project(tmp_path, services_factory):
    source = tmp_path / "source.xml"
    fixture = Path(__file__).parents[2] / "contracts" / "io" / "fixtures" / "xt-small.xml"
    source.write_bytes(fixture.read_bytes())
    services = services_factory("export")
    context = RequestContext("gui", run_id="archive")
    _ok(
        services.gui_project_commands.create_project(
            ProjectProvisioningRequest("可移植工程", source=ProjectSourceRequest(str(source), FormatId.XML_XT)), context
        )
    )
    active = services.project_lifecycle.active
    project_ref, first_ref = active.project_ref, active.formal_variant_ref
    key = active.variant.snapshot().entries[0].entry_key
    commands = services.gui_project_commands
    _ok(commands.update_entry(key, context, translation="初版译文", stage=3))
    _ok(commands.replace_labels({key: {"reviewed"}}, {"reviewed": {"name": "已审核", "color": "#123456"}}, context))
    _ok(commands.save(context))
    _ok(commands.save_snapshot("初版快照", context))
    _ok(commands.create_variant("润色分支", context, copy_active=True))
    second_ref = services.project_lifecycle.active.formal_variant_ref
    _ok(commands.update_entry(key, context, translation="润色译文", stage=1))
    _ok(commands.save(context))
    _ok(commands.save_snapshot("润色快照", context))
    bundle = tmp_path / "project.transbridge"
    services.project_archive.export_project(str(bundle), context)
    return SimpleNamespace(
        services=services,
        context=context,
        bundle=bundle,
        source=source,
        project_ref=project_ref,
        variant_refs=(first_ref, second_ref),
        key=key,
    )


def test_roundtrip_preserves_two_variants_named_history_and_source_order(services_factory, exported_project):
    origin = exported_project
    origin.source.unlink()
    target = services_factory("import")
    previous = origin.bundle.read_bytes()
    installed = Path(target.project_archive.import_project(str(origin.bundle), origin.context))

    assert origin.bundle.read_bytes() == previous
    assert target.project_lifecycle.active is None
    _ok(target.current_project_opener.open_path(installed, origin.context))
    expected = ProjectStore(Path(origin.services.projects.path_for(origin.project_ref))).summary()
    assert ProjectStore(installed).summary() == expected
    source_history, target_history = ProjectHistory(origin.services.projects), ProjectHistory(target.projects)
    for ref in origin.variant_refs:
        assert target.variants.read_snapshot(ref) == origin.services.variants.read_snapshot(ref)
        assert target_history.list(ref) == source_history.list(ref)
        for item in source_history.list(ref):
            assert target_history.load(item.identity, ref) == source_history.load(item.identity, ref)


@pytest.mark.parametrize("kind", ["directory", "file"])
def test_import_rejects_unpackaged_inputs_without_publishing(tmp_path, services_factory, kind):
    target = services_factory("unsupported")
    source = tmp_path / "invalid.transbridge"
    if kind == "directory":
        source.mkdir()
    else:
        source.write_bytes(b"unpackaged data")
    before = _file_state(target.root)
    for operation in (target.project_archive.inspect_import_conflicts, target.project_archive.import_project):
        with pytest.raises(UnsupportedProjectFormat):
            operation(str(source), RequestContext("gui"))
        assert _file_state(target.root) == before


def test_full_import_revalidates_after_conflict_inspection(tmp_path, services_factory, exported_project):
    origin = exported_project
    target = services_factory("changed")
    path = tmp_path / "selected.transbridge"
    path.write_bytes(origin.bundle.read_bytes())
    assert target.project_archive.inspect_import_conflicts(str(path), origin.context).project_id == (
        origin.project_ref.identity.value
    )
    with zipfile.ZipFile(path, "a") as bundle:
        bundle.writestr("extra-unapproved-member", b"invalid")
    before = _file_state(target.root)

    with pytest.raises(UnsupportedProjectFormat):
        target.project_archive.import_project(str(path), origin.context)

    assert _file_state(target.root) == before


@pytest.mark.parametrize("conflict", ["identity", "name"])
def test_existing_project_identity_or_name_never_overwritten(services_factory, exported_project, conflict):
    origin = exported_project
    target = services_factory("conflict")
    if conflict == "identity":
        target.project_archive.import_project(str(origin.bundle), origin.context)
    else:
        _ok(target.gui_project_commands.create_project(ProjectProvisioningRequest("可移植工程"), origin.context))
    before = _file_state(target.root)
    active = target.project_lifecycle.active

    with pytest.raises(ProjectStorageConflict):
        target.project_archive.import_project(str(origin.bundle), origin.context)

    assert _file_state(target.root) == before
    assert target.project_lifecycle.active is active


def test_identity_conflict_can_be_imported_as_a_reidentified_copy(services_factory, exported_project):
    origin = exported_project
    target = services_factory("copy")
    original = Path(target.project_archive.import_project(str(origin.bundle), origin.context))
    before = original.read_bytes()
    copied = Path(
        target.project_archive.import_project(
            str(origin.bundle), origin.context, requested_name="可移植工程副本", copy_on_identity_conflict=True
        )
    )
    assert copied != original
    assert original.read_bytes() == before
    original_summary, copy_summary = ProjectStore(original).summary(), ProjectStore(copied).summary()
    assert copy_summary.identity != original_summary.identity
    assert copy_summary.name == "可移植工程副本"
    assert {item.identity for item in copy_summary.variants}.isdisjoint(
        item.identity for item in original_summary.variants
    )
    assert ProjectStore(copied).read_page(copy_summary.active_variant) == ProjectStore(original).read_page(
        original_summary.active_variant
    )
    histories = ProjectHistory(target.projects)
    for old, new in zip(original_summary.variants, copy_summary.variants, strict=True):
        assert old.name == new.name
        old_ref = VariantRef(old.identity, original_summary.identity)
        new_ref = VariantRef(new.identity, copy_summary.identity)
        snapshots = histories.list(old_ref)
        assert snapshots and histories.list(new_ref) == snapshots
        for snapshot in snapshots:
            original_snapshot = histories.load(snapshot.identity, old_ref)
            assert histories.load(snapshot.identity, new_ref).entries == original_snapshot.entries


def test_renamed_import_preserves_identity_and_current_project(services_factory, exported_project):
    origin = exported_project
    target = services_factory("rename")
    _ok(target.gui_project_commands.create_project(ProjectProvisioningRequest("可移植工程"), origin.context))
    active = target.project_lifecycle.active
    before = target.current_project_opener.prepare_active(origin.context).value.project_ref
    inspection = target.project_archive.inspect_import(str(origin.bundle), origin.context)
    assert inspection.name_conflict and not inspection.identity_conflict

    imported = target.project_archive.import_project(str(origin.bundle), origin.context, requested_name="Imported")

    assert ProjectStore(Path(imported)).summary().identity == origin.project_ref.identity
    assert {item.name for item in target.project_catalog.list_projects().projects} == {"可移植工程", "Imported"}
    assert target.project_lifecycle.active is active
    assert target.current_project_opener.prepare_active(origin.context).value.project_ref == before


def test_import_publish_failure_preserves_existing_project_and_cleans_staging(
    services_factory, exported_project, monkeypatch
):
    origin = exported_project
    target = services_factory("write-fault")
    _ok(target.gui_project_commands.create_project(ProjectProvisioningRequest("Active"), origin.context))
    before = _file_state(target.root)
    active = target.project_lifecycle.active
    replace = Path.rename

    def fail_publication(path, destination):
        if path.name.startswith(".package-stage-"):
            raise OSError("injected publication failure")
        return replace(path, destination)

    monkeypatch.setattr(Path, "rename", fail_publication)
    with pytest.raises(OSError, match="injected publication failure"):
        target.project_archive.import_project(str(origin.bundle), origin.context)

    assert _file_state(target.root) == before
    assert not list((Path(target.root) / "project-store-v1").glob(".package-stage-*"))
    assert target.project_lifecycle.active is active


def test_export_preserves_old_output_when_project_is_dirty(tmp_path, exported_project):
    origin = exported_project
    origin.source.write_text("Modified outside the project", encoding="utf-8")
    destination = tmp_path / "durable.transbridge"
    origin.services.project_archive.export_project(str(destination), origin.context)
    previous = destination.read_bytes()
    _ok(origin.services.gui_project_commands.update_entry(origin.key, origin.context, translation="Unsaved"))

    with pytest.raises(ValueError, match="保存"):
        origin.services.project_archive.export_project(str(destination), origin.context)

    assert destination.read_bytes() == previous


def test_concurrent_imports_do_not_publish_duplicate_project_names(
    tmp_path, services_factory, exported_project, monkeypatch
):
    origin = exported_project
    _ok(origin.services.gui_project_commands.create_project(ProjectProvisioningRequest("Other"), origin.context))
    second_bundle = tmp_path / "second.transbridge"
    origin.services.project_archive.export_project(str(second_bundle), origin.context)
    target = services_factory("concurrent")
    ready = Barrier(2)
    rename = Path.rename

    def overlap_publication(path, destination):
        if path.name.startswith(".package-stage-"):
            try:
                ready.wait(timeout=2)
            except BrokenBarrierError:
                # A serialized publisher can finish while its competitor waits for the lock.
                pass
        return rename(path, destination)

    def install(bundle):
        try:
            target.project_archive.import_project(str(bundle), origin.context, requested_name="Same")
        except ProjectStorageConflict:
            return False
        return True

    monkeypatch.setattr(Path, "rename", overlap_publication)
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(install, (origin.bundle, second_bundle)))

    assert results.count(True) == 1
    assert [item.name for item in target.project_catalog.list_projects().projects] == ["Same"]
