from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from itertools import count
from pathlib import Path
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

from transbridge.application.contracts import DomainError
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.bootstrap import build_runtime
from transbridge.bootstrap.adapters import DenyByDefaultSecurity, NullSecretStore, SystemClock
from transbridge.bootstrap.runtime import RuntimePorts


@pytest.fixture
def runtime(tmp_path):
    ids = count()
    app = build_runtime(
        settings={
            "persistence_v2_root": tmp_path / "data",
            "ui_config_path": tmp_path / "config.toml",
            "translation_memory_root": tmp_path / "memory",
        },
        ports=RuntimePorts(
            clock=SystemClock(),
            ids=SimpleNamespace(new_id=lambda: f"i{next(ids)}"),
            secrets=NullSecretStore(),
            security=DenyByDefaultSecurity(),
        ),
    )
    yield app
    assert app.close().is_success


def _project(app, name):
    persistence = app.use_cases.resolve("persistence_v2")
    context = app.context("gui", run_id=f"create-{name}")
    result = persistence.gui_project_commands.create_project(ProjectProvisioningRequest(name), context)
    assert result.is_success, result.diagnostics
    active = persistence.project_lifecycle.active
    project_id = active.project_ref.identity.value
    context = app.context("gui", project_id=project_id, variant_id=active.formal_variant_ref.identity.value)
    factory = app.use_cases.resolve("terminology_profile_service_factory")
    profiles = factory.profile_service_for(project_id)
    return persistence, active, context, profiles, factory.repositories.for_project(project_id)


def test_delete_closes_only_target_project_and_retained_readers_cannot_recreate_it(runtime):
    _, other, _, other_profiles, other_repository = _project(runtime, "other")
    other_profile = other_profiles.create(other.project_ref.identity.value, "Other profile")
    persistence, active, context, profiles, repository = _project(runtime, "target")
    profiles.create(context.project_id, "Target profile")
    # Keep a partially consumed SQLite statement alive across deletion as a UI reader could.
    cursor = repository._connection.execute("SELECT 1 UNION ALL SELECT 2")
    assert cursor.fetchone()[0] == 1
    result = persistence.project_management.delete(context.project_id, context, expected_name="target")

    assert result.is_success, result.diagnostics
    assert not repository.path.exists()
    assert not Path(persistence.projects.path_for(active.project_ref)).exists()
    assert other_profiles.list_profiles(other.project_ref.identity.value) == (other_profile,)
    assert other_repository.read_cache_token()
    with pytest.raises(sqlite3.ProgrammingError, match="关闭"):
        profiles.list_profiles(context.project_id)
    with pytest.raises(DomainError):
        runtime.use_cases.resolve("terminology_repository").for_project(context.project_id)
    assert not repository.path.exists()


def test_failed_delete_restores_old_profile_service_and_connection_for_retry(runtime, monkeypatch):
    persistence, active, context, profiles, repository = _project(runtime, "recoverable")
    profile = profiles.create(context.project_id, "Existing profile")
    token = profiles.read_cache_token()
    watched = (
        Path(persistence.projects.path_for(active.project_ref)),
        Path(persistence.variants.path_for(active.formal_variant_ref)),
        Path(persistence.root) / "project-catalog.json",
        Path(persistence.root) / "active-project.json",
        repository.path,
    )
    preimages = {path: path.read_bytes() for path in watched}
    original_remove = persistence.filesystem.remove
    removed = []

    def fail_project_remove(path, *, missing_ok=False):
        if Path(path) == watched[0]:
            raise OSError("injected project deletion failure")
        original_remove(path, missing_ok=missing_ok)
        removed.append(Path(path))

    with monkeypatch.context() as patch:
        patch.setattr(persistence.filesystem, "remove", fail_project_remove)
        result = persistence.project_management.delete(context.project_id, context, expected_name="recoverable")
    assert not result.is_success
    assert repository.path in removed
    assert all(path.read_bytes() == data for path, data in preimages.items())
    assert profiles.read_cache_token() != token
    renamed = profiles.rename(profile.profile_id, "Still editable")
    assert profiles.list_profiles(context.project_id) == (renamed,)
    assert persistence.gui_project_commands.save(context).is_success
    assert runtime.use_cases.resolve("terminology_repository").for_project(context.project_id) is repository

    retried = persistence.project_management.delete(context.project_id, context, expected_name="recoverable")
    assert retried.is_success, retried.diagnostics
    assert not repository.path.exists()


def test_delete_waits_for_inflight_project_transaction_without_closing_other_projects(runtime, monkeypatch):
    _, other, _, other_profiles, _ = _project(runtime, "other")
    persistence, _, context, profiles, repository = _project(runtime, "busy")
    entered, release, deleting = Event(), Event(), Event()
    original_scope = repository.deletion_scope

    @contextmanager
    def observe_deletion():
        deleting.set()
        with original_scope():
            yield

    monkeypatch.setattr(repository, "deletion_scope", observe_deletion)

    def write():
        with repository._lock, repository.transaction():
            entered.set()
            assert release.wait(5)
            return profiles.create(context.project_id, "Completes before deletion")

    with ThreadPoolExecutor(max_workers=2) as workers:
        write_result = workers.submit(write)
        assert entered.wait(5)
        delete_result = workers.submit(
            persistence.project_management.delete, context.project_id, context, expected_name="busy"
        )
        try:
            assert deleting.wait(5)
            assert not delete_result.done()
            assert other_profiles.list_profiles(other.project_ref.identity.value) == ()
        finally:
            release.set()
        assert write_result.result(timeout=5).name == "Completes before deletion"
        deleted = delete_result.result(timeout=5)
        assert deleted.is_success, deleted.diagnostics
    assert not repository.path.exists()


def test_unowned_pending_transaction_rejects_deletion_without_rolling_it_back(runtime):
    persistence, _, context, profiles, repository = _project(runtime, "pending")
    repository._connection.execute("BEGIN IMMEDIATE")
    try:
        profile = profiles.create(context.project_id, "Pending profile")
        result = persistence.project_management.delete(context.project_id, context, expected_name="pending")
        assert not result.is_success
        assert result.diagnostics[0].code == "PROJECT_TERMINOLOGY_BUSY"
        assert repository._connection.in_transaction
        assert profiles.list_profiles(context.project_id) == (profile,)
    finally:
        repository._connection.rollback()
    assert profiles.list_profiles(context.project_id) == ()
    assert persistence.project_management.delete(context.project_id, context, expected_name="pending").is_success


def test_incomplete_filesystem_rollback_never_recreates_an_empty_terminology_database(runtime):
    _, _, context, profiles, repository = _project(runtime, "incomplete-rollback")
    profiles.create(context.project_id, "Must not disappear silently")
    repositories = runtime.use_cases.resolve("terminology_repository")
    with pytest.raises(ExceptionGroup, match="无法重新打开") as failure:
        with repositories.deletion_scope(context.project_id):
            repository.path.unlink()
            raise OSError("injected incomplete filesystem rollback")
    assert isinstance(failure.value.exceptions[0], OSError)
    assert isinstance(failure.value.exceptions[1], FileNotFoundError)
    assert not repository.path.exists()
    with pytest.raises(sqlite3.ProgrammingError):
        profiles.list_profiles(context.project_id)
    assert not repository.path.exists()


def test_reimport_same_project_identity_gets_fresh_services_without_reactivating_deleted_references(runtime, tmp_path):
    persistence, _, context, profiles, repository = _project(runtime, "archived")
    profile = profiles.create(context.project_id, "Archived profile")
    bundle = tmp_path / "restore.transbridge"
    persistence.project_archive.export_project(str(bundle), context)
    assert persistence.project_management.delete(context.project_id, context, expected_name="archived").is_success
    persistence.project_archive.import_project(str(bundle), context)

    factory = runtime.use_cases.resolve("terminology_profile_service_factory")
    restored_repository = factory.repositories.for_project(context.project_id)
    restored_profiles = factory.profile_service_for(context.project_id)
    assert restored_repository is not repository
    assert restored_profiles is not profiles
    assert restored_profiles.list_profiles(context.project_id) == (profile,)
    with pytest.raises(sqlite3.ProgrammingError):
        profiles.list_profiles(context.project_id)
    with pytest.raises(sqlite3.ProgrammingError):
        repository.read_cache_token()
