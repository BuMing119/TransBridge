"""Packages keep selected source history when copied without original inputs."""

from pathlib import Path

from tests.application.projects import test_source_update as update_support
from tests.persistence.v2 import test_project_archive as archive_support
from transbridge.persistence.project_store.history import ProjectHistory
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import VariantRef

project = update_support.project
services_factory = archive_support.services_factory


def _historical_sources(services, project, ref, context):
    history = ProjectHistory(services.projects)
    assert services.current_project_opener.open_path(project.path, context).is_success
    assert services.gui_project_commands.switch_v2(
        services.project_lifecycle.active.project_ref, ref, context
    ).is_success
    (snapshot,) = history.list(ref)
    return tuple(
        (entry.snapshot.original, entry.state.translation) for entry in history.read_page(snapshot.identity, ref)
    )


def test_updated_project_bundle_keeps_preimage_sources_after_copy_and_reexport(tmp_path, project, services_factory):
    origin, service, context, source, project_path, _source_id = project
    active = origin.project_lifecycle.active
    old_variant = active.formal_variant_ref
    preview, replacement = update_support._prepare(project)
    updated = service.commit(preview.token, context)
    assert updated.is_success, updated.diagnostics
    source.unlink()
    replacement.unlink()
    bundle = tmp_path / "history.transbridge"
    assert origin.current_project_opener.open_path(project_path, context).is_success
    origin.project_archive.export_project(str(bundle), context, include_snapshots=True)
    expected = ProjectHistory(origin.projects).list(old_variant)

    target = services_factory("restored")
    target.project_archive.import_project(str(bundle), context)
    copy_path = target.project_archive.import_project(
        str(bundle), context, copy_on_identity_conflict=True, requested_name="Copy with history"
    )
    copied = ProjectStore(Path(copy_path))
    summary = copied.summary()
    assert summary.identity != old_variant.project_id
    copied_variant = VariantRef(summary.active_variant, summary.identity)
    copied_history = ProjectHistory(target.projects)
    assert [item.name for item in copied_history.list(copied_variant)] == [item.name for item in expected]
    copied_contents = _historical_sources(target, copied, copied_variant, context)
    assert copied_contents == (("One", "译文"), ("Two", "译文"), ("Three", "译文"))

    again = tmp_path / "copy-history.transbridge"
    target.project_archive.export_project(str(again), context)
    destination = services_factory("reimport")
    installed = ProjectStore(Path(destination.project_archive.import_project(str(again), context)))
    assert _historical_sources(destination, installed, copied_variant, context) == copied_contents
