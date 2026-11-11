from pathlib import Path
import sqlite3

import pytest

from tests.path_fixtures import deep_directory
from transbridge.application.io.publish.filesystem import OsPublishFilesystem
from transbridge.persistence.project_store.connection import project_connection
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.task_store.connection import transaction
from transbridge.persistence.v2.ids import ProjectId, VariantId


@pytest.mark.parametrize("extended", [False, True])
def test_task_database_long_path_create_reopen_and_rollback(tmp_path, extended):
    path = deep_directory(tmp_path, 270) / "tasks.sqlite3"
    if extended:
        path = Path(OsPublishFilesystem().canonicalize(str(path)))
    with transaction(path) as db:
        assert db is None
    with transaction(path, write=True) as db:
        assert db.execute("SELECT 1").fetchone()[0] == 1
    with transaction(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="abort"):
        with transaction(path, write=True) as db:
            db.execute("PRAGMA user_version = 99")
            raise RuntimeError("abort")
    with transaction(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version


@pytest.mark.parametrize("extended", [False, True])
def test_project_long_path_publication_reads_and_write_lease(tmp_path, extended):
    project_id = ProjectId("path-project")
    path = project_path(deep_directory(tmp_path, 250), project_id)
    if extended:
        path = Path(OsPublishFilesystem().canonicalize(str(path)))
    store = ProjectStore(path)
    store.create(project_id, "长路径工程", VariantId("main"), "Main")
    assert ProjectStore(path).summary().name == "长路径工程"
    with project_connection(path, write=True) as db:
        db.execute("UPDATE project SET name = 'Updated'")
    assert ProjectStore(path).summary().name == "Updated"
