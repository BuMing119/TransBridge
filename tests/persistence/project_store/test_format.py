from contextlib import closing
import sqlite3

import pytest

from transbridge.application.projects.storage_contracts import SaveBatch
from transbridge.persistence.project_store.format import PROJECT_FORMAT_VERSION, UnsupportedProjectFormat
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectId, VariantId


@pytest.mark.parametrize("kind", ["missing", "empty", "legacy-json", "foreign-sqlite"])
def test_open_rejects_non_project_files_without_changing_them(tmp_path, kind):
    path = tmp_path / "project.sqlite3"
    if kind == "empty":
        path.touch()
    elif kind == "legacy-json":
        path.write_bytes(b'{"schema_version": 2, "kind": "project", "data": {"sources": []}}')
    elif kind == "foreign-sqlite":
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE other_app (value TEXT)")
            connection.execute("INSERT INTO other_app VALUES ('keep me')")
            connection.commit()

    before = {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    with pytest.raises(UnsupportedProjectFormat, match="Unsupported project format"):
        ProjectStore(path).summary()
    assert {file.name: file.read_bytes() for file in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "tampering",
    [
        f"PRAGMA user_version = {PROJECT_FORMAT_VERSION + 1}",
        "UPDATE project SET format = 'different project format'",
        "DROP TABLE source_entry",
        "CREATE TRIGGER untrusted_trigger AFTER UPDATE ON project BEGIN DELETE FROM entry_state; END",
    ],
    ids=["future-version", "wrong-metadata", "missing-table", "untrusted-trigger"],
)
def test_open_rejects_modified_project_schema_or_identity_without_repairing_it(tmp_path, tampering):
    path = tmp_path / "project.sqlite3"
    ProjectStore(path).create(ProjectId("format-case"), "Format case", VariantId("main"), "Main")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(tampering)
        connection.commit()

    before = {file.name: file.read_bytes() for file in tmp_path.iterdir()}
    with pytest.raises(UnsupportedProjectFormat, match="Unsupported project format"):
        ProjectStore(path).summary()
    assert {file.name: file.read_bytes() for file in tmp_path.iterdir()} == before


def test_direct_commit_refuses_untrusted_schema_without_creating_a_receipt(tmp_path):
    path = tmp_path / "project.sqlite3"
    project_id, variant = ProjectId("direct-commit"), VariantId("main")
    store = ProjectStore(path)
    store.create(project_id, "Direct commit", variant, "Main")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("""
            CREATE TRIGGER untrusted_trigger AFTER INSERT ON commit_receipt
            BEGIN UPDATE project SET name = 'unexpected side effect'; END
        """)
        connection.commit()
    before = path.read_bytes()

    with pytest.raises(UnsupportedProjectFormat, match="Unsupported project format"):
        store.commit(SaveBatch(project_id, variant, 0, 0, (), operation_id="record-result"))

    assert path.read_bytes() == before
