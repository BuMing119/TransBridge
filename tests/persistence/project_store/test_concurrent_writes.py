from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from threading import Event

import pytest

from transbridge.persistence.project_store.connection import project_connection, project_mutation_lease
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.persistence.v2.ids import ProjectId, VariantId
from transbridge.persistence.v2.models import ReadOnlyWriteRefused


def _create_project(root, name):
    project_id = ProjectId(name)
    path = project_path(root, project_id)
    ProjectStore(path).create(project_id, name, VariantId("main"), "Main")
    return path


def test_local_save_waits_for_same_project_writer_only(tmp_path):
    path = _create_project(tmp_path, "first")
    other = _create_project(tmp_path, "second")
    started = Event()

    def save():
        started.set()
        with project_connection(path, write=True) as db:
            db.execute("UPDATE project SET name = 'Saved'")

    with ThreadPoolExecutor(max_workers=2) as executor:
        with project_connection(path, write=True):
            pending = executor.submit(save)
            assert started.wait(5)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.2)
            assert executor.submit(_try_write, other).result(timeout=5) == "saved"
        pending.result(timeout=5)
    assert ProjectStore(path).summary().name == "Saved"
    assert ProjectStore(other).summary().name == "Saved"


def _try_write(path):
    try:
        with project_connection(path, write=True) as db:
            db.execute("UPDATE project SET name = 'Saved'")
    except ReadOnlyWriteRefused as exc:
        return exc.code
    return "saved"


def test_external_writer_is_refused_until_outermost_lease_exits(tmp_path):
    path = _create_project(tmp_path, "shared")
    with ProcessPoolExecutor(max_workers=1, mp_context=get_context("spawn")) as executor:
        with project_mutation_lease(path):
            with project_connection(path, write=True):
                pass
            assert executor.submit(_try_write, path).result(timeout=15) == "PROJECT_WRITER_BUSY"
        assert executor.submit(_try_write, path).result(timeout=15) == "saved"
