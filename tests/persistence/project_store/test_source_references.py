from contextlib import closing
import json
import sqlite3
from zipfile import ZipFile

from tests.persistence.project_store.test_source_extraction import _create_project
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects.storage_contracts import EntryStateChange, SaveBatch
from transbridge.persistence.project_store.archive import ProjectPackage
from transbridge.persistence.project_store.catalog import ActiveProjectPreference, ProjectCatalog
from transbridge.persistence.project_store.format import PROJECT_FILENAME, PROJECT_FORMAT, PROJECT_FORMAT_VERSION
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2 import OsPersistenceFilesystem


def test_source_reference_can_be_added_to_existing_project_and_retains_revision_guards(tmp_path):
    plugin = write_plugin(tmp_path / "Source.esp", [(0x800, "Greeter", "Welcome")])
    root = tmp_path / "source"
    database, variant = _create_project(root, plugin, FormatId.PLUGIN_SSE, role="primary")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TABLE source_reference")
        connection.commit()
    before = database.read_bytes()
    workspace = WorkspaceProjects(root)
    project = ProjectStore(database)
    (info,) = project.sources(native_fields=False)
    (entry,) = project.read_page(variant)
    token = project.read_token(variant)
    assert not info.reference_path and database.read_bytes() == before

    def replace_reference(expected_path, path, *, expected_token=token, source_id=info.namespace.value):
        return workspace.update_source_reference(
            token.project_id,
            source_id,
            info.namespace,
            expected_fingerprint=info.fingerprint,
            expected_path=expected_path,
            reference_path=path,
            token=expected_token,
        )

    missing_path = str(tmp_path / "missing" / plugin.name)
    assert not replace_reference("", missing_path, source_id="different-source")
    assert database.read_bytes() == before
    assert replace_reference("", missing_path)
    assert replace_reference(missing_path, missing_path)
    assert not replace_reference("", str(plugin))
    assert project.read_token(variant) == token
    assert project.sources()[0].reference_path == missing_path

    project.commit(
        SaveBatch(
            token.project_id,
            token.variant_id,
            token.project_revision,
            token.variant_revision,
            (EntryStateChange(entry.state.entry_key, (("translation", "Translated"),)),),
        )
    )
    assert not replace_reference(missing_path, str(plugin))
    assert project.sources()[0].reference_path == missing_path
    assert replace_reference(missing_path, str(plugin), expected_token=project.read_token(variant))
    assert project.sources()[0].reference_path == str(plugin)


def test_old_project_package_without_references_is_admitted(tmp_path):
    plugin = write_plugin(tmp_path / "Source.esp", [(0x800, "Greeter", "Welcome")])
    database, variant = _create_project(tmp_path / "source", plugin, FormatId.PLUGIN_SSE, role="primary")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("DROP TABLE source_reference")
        connection.commit()
    project_id = ProjectStore(database).summary().identity
    package = tmp_path / "old.transbridge"
    with ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({
                "format": PROJECT_FORMAT,
                "version": PROJECT_FORMAT_VERSION,
                "project_id": project_id.value,
                "members": [PROJECT_FILENAME],
            }),
        )
        archive.write(database, PROJECT_FILENAME)
    workspace = WorkspaceProjects(tmp_path / "destination")
    catalog = ProjectCatalog(workspace, ActiveProjectPreference(workspace.root, OsPersistenceFilesystem()))
    projects = ProjectPackage(workspace, catalog, lifecycle=None)
    context = RequestContext("legacy-archive-test")

    imported = ProjectStore(projects.import_project(str(package), context))
    assert imported.sources()[0].reference_path == ""
    assert imported.read_page(variant)[0].snapshot.original == "Welcome"
