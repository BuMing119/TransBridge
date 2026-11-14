from contextlib import closing
import json
import sqlite3
from zipfile import ZipFile

import pytest

from tests.persistence.project_store.test_source_extraction import _create_project
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.persistence.project_store.archive import ProjectPackage
from transbridge.persistence.project_store.catalog import ActiveProjectPreference, ProjectCatalog
from transbridge.persistence.project_store.format import (
    PROJECT_FILENAME,
    PROJECT_FORMAT,
    PROJECT_FORMAT_VERSION,
    UnsupportedProjectFormat,
)
from transbridge.persistence.project_store.records import WorkspaceProjects
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2 import OsPersistenceFilesystem
from transbridge.persistence.v2.ids import ProjectId, VariantId


@pytest.mark.parametrize("kind", ["legacy", "future", "extra-member", "duplicate-member", "unsafe-schema"])
def test_project_package_rejects_unsupported_contents_without_rewriting_or_publishing(tmp_path, kind):
    project_id = ProjectId("package-fixture")
    database = tmp_path / PROJECT_FILENAME
    ProjectStore(database).create(project_id, "Fixture", VariantId("main"), "Main")
    if kind == "unsafe-schema":
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("""
                CREATE TRIGGER unauthorized AFTER UPDATE ON project
                BEGIN DELETE FROM named_snapshot; END
            """)
            connection.commit()
    manifest = {
        "format": PROJECT_FORMAT,
        "version": PROJECT_FORMAT_VERSION + int(kind == "future"),
        "project_id": project_id.value,
        "members": [PROJECT_FILENAME],
    }
    package = tmp_path / "candidate.transbridge"
    with ZipFile(package, "w") as archive:
        if kind == "legacy":
            archive.writestr("manifest.json", json.dumps({"schema_version": 2, "project_id": project_id.value}))
            archive.writestr("project.json", '{"schema_version":2,"entity_type":"project"}')
        else:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.write(database, PROJECT_FILENAME)
            if kind == "extra-member":
                archive.writestr("../escaped.sqlite3", b"untrusted contents")
            if kind == "duplicate-member":
                with pytest.warns(UserWarning, match="Duplicate name"):
                    archive.write(database, PROJECT_FILENAME)

    workspace = WorkspaceProjects(tmp_path / "destination")
    active = ActiveProjectPreference(workspace.root, OsPersistenceFilesystem())
    catalog = ProjectCatalog(workspace, active)
    projects = ProjectPackage(workspace, catalog, lifecycle=None)
    original = package.read_bytes()
    context = RequestContext("archive-test")

    with pytest.raises(UnsupportedProjectFormat):
        projects.inspect_import(str(package), context)
    with pytest.raises(UnsupportedProjectFormat):
        projects.import_project(str(package), context)

    assert package.read_bytes() == original
    assert not catalog.list_projects().projects
    assert not tuple(workspace.directory.iterdir())
    assert not (tmp_path / "escaped.sqlite3").exists()


@pytest.mark.parametrize(
    "tampering",
    [
        (("UPDATE project SET settings = ?", ('{"prepared_source":{"plugin_bytes":"forbidden"}}',)),),
        (("UPDATE source SET name = ?", ("C:/private/Historical.esp",)),),
        (("UPDATE source_reference SET path = ?", ("invalid\x00path",)),),
        (("UPDATE source_entry SET metadata = ?", ('{"io.locator":["record",["C:/private/Historical.esp"]]}',)),),
        (("INSERT INTO source_format_record SELECT id, 0, '<ESP/>', NULL FROM source", ()),),
        (
            ("INSERT INTO named_snapshot VALUES ('historical', 'main', 'Historical', 0, '{}', '{}')", ()),
            (
                "INSERT INTO snapshot_entry_state SELECT 'historical', id, '', 0, '[]', '[]', 0, 0, '[]', '[]' "
                "FROM source_entry",
                (),
            ),
        ),
    ],
    ids=[
        "unknown-settings",
        "historical-source-name",
        "invalid-reference-text",
        "nested-source-path",
        "plugin-template",
        "wrong-history-source",
    ],
)
def test_valid_sqlite_schema_still_rejects_unapproved_portable_content(tmp_path, tampering):
    plugin = write_plugin(tmp_path / "Source.esp", [(0x800, "Greeter", "Welcome")])
    database, _variant = _create_project(tmp_path / "source", plugin, FormatId.PLUGIN_SSE, role="primary")
    project_id = ProjectStore(database).summary().identity
    package = tmp_path / "candidate.transbridge"
    manifest = {
        "format": PROJECT_FORMAT,
        "version": PROJECT_FORMAT_VERSION,
        "project_id": project_id.value,
        "members": [PROJECT_FILENAME],
    }
    workspace = WorkspaceProjects(tmp_path / "destination")
    catalog = ProjectCatalog(workspace, ActiveProjectPreference(workspace.root, OsPersistenceFilesystem()))
    projects = ProjectPackage(workspace, catalog, lifecycle=None)
    context = RequestContext("archive-test")

    def package_database():
        with ZipFile(package, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.write(database, PROJECT_FILENAME)

    package_database()
    assert projects.inspect_import(str(package), context).project_id == project_id.value
    with closing(sqlite3.connect(database)) as connection:
        for sql, parameters in tampering:
            connection.execute(sql, parameters)
        connection.commit()
    package_database()
    before = package.read_bytes()

    with pytest.raises(UnsupportedProjectFormat):
        projects.inspect_import(str(package), context)
    with pytest.raises(UnsupportedProjectFormat):
        projects.import_project(str(package), context)

    assert package.read_bytes() == before
    assert not catalog.list_projects().projects
    assert not tuple(workspace.directory.iterdir())
