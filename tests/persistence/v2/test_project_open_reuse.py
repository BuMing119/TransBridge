"""Preparing an SQLite project separates data reads from final activation."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.persistence.v2.test_project_archive import (
    exported_project as exported_fixture,
    services_factory as services_fixture,
)
from transbridge.persistence.project_store.records import WorkspaceProjects

exported_project = exported_fixture
services_factory = services_fixture


def test_prepared_open_across_threads_reuses_project_content(exported_project, services_factory, monkeypatch):
    origin = exported_project
    target = services_factory("target")
    path = target.project_archive.import_project(str(origin.bundle), origin.context)
    reads = []
    original = WorkspaceProjects.candidate

    def counted(projects, transition):
        reads.append(transition)
        return original(projects, transition)

    monkeypatch.setattr(WorkspaceProjects, "candidate", counted)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(target.current_project_opener.prepare_path, path, origin.context).result()
    assert result.is_success, result.diagnostics
    assert len(reads) == 1
    views = []
    activated = target.current_project_opener.activate(result.value, origin.context, prepare_view=views.append)
    assert activated.is_success, activated.diagnostics
    assert len(reads) == 1
    final = target.project_projection.snapshot()
    assert views[0].values["entries"] is final.values["entries"]
    assert target.gui_project_commands.save(origin.context).is_success
    assert target.project_projection.snapshot() is final
    assert len(reads) == 1


@pytest.mark.parametrize("kind", ["project", "variant"])
def test_prepared_open_refuses_new_authoritative_revision(exported_project, services_factory, kind):
    origin = exported_project
    target = services_factory("target")
    path = target.project_archive.import_project(str(origin.bundle), origin.context)
    prepared = target.current_project_opener.prepare_path(path, origin.context)
    assert prepared.is_success, prepared.diagnostics
    writer = services_factory("target")
    assert writer.current_project_opener.open_path(path, origin.context).is_success
    if kind == "project":
        assert writer.project_management.rename("New name", origin.context).is_success
    else:
        assert writer.gui_project_commands.update_entry(origin.key, origin.context, translation="New text").is_success
        assert writer.gui_project_commands.save(origin.context).is_success
    snapshot = writer.variants.read_snapshot(prepared.value.variant_ref)

    result = target.current_project_opener.activate(prepared.value, origin.context)

    assert not result.is_success
    assert target.project_lifecycle.active is None
    assert target.variants.read_snapshot(prepared.value.variant_ref) == snapshot
    assert target.projects.read_snapshot(prepared.value.project_ref) == writer.projects.read_snapshot(
        prepared.value.project_ref
    )
