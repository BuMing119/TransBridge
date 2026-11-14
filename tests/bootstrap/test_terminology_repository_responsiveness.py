from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import DomainError
from transbridge.bootstrap import terminology_storage


def test_cache_path_is_available_while_repository_open_checks_integrity(tmp_path, monkeypatch):
    repositories = terminology_storage.ProjectTerminologyRepositories(tmp_path)
    monkeypatch.setattr(repositories._projects, "exists", lambda _ref: True)
    entered, release = Event(), Event()
    opened = SimpleNamespace(
        localization_profiles=SimpleNamespace(bind_project_selection=lambda *_args: None), close=lambda: None
    )

    def open_repository(*_args):
        entered.set()
        assert release.wait(5)
        return opened

    monkeypatch.setattr(terminology_storage.SqliteTerminologyRepository, "open", open_repository)
    with ThreadPoolExecutor(max_workers=2) as workers:
        initialization = workers.submit(repositories.for_project, "project")
        assert entered.wait(5)
        cache_path = workers.submit(repositories.alignment_cache_path, "project")
        try:
            # Completion must not depend on releasing the database opener.
            assert cache_path.result(timeout=1).name == "project.sqlite3"
            assert not initialization.done()
        finally:
            release.set()
        assert initialization.result(timeout=5) is opened
    repositories.close()


@pytest.mark.parametrize("succeeds", [False, True])
def test_cache_path_still_rejects_deleting_projects_and_recovers_after_rollback(tmp_path, succeeds):
    repositories = terminology_storage.ProjectTerminologyRepositories(tmp_path)
    expected = nullcontext() if succeeds else pytest.raises(RuntimeError, match="rollback")
    with expected, repositories.deletion_scope("project"):
        with pytest.raises(DomainError, match="定位缓存"):
            repositories.alignment_cache_path("project")
        if not succeeds:
            raise RuntimeError("rollback")
    if succeeds:
        with pytest.raises(DomainError, match="定位缓存"):
            repositories.alignment_cache_path("project")
    else:
        assert repositories.alignment_cache_path("project").name == "project.sqlite3"
