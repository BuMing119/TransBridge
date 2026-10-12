"""Real-file publication, interrupted-process recovery and root lease contracts."""

from dataclasses import replace
from functools import partial
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread

import pytest

from transbridge.application.contracts import DomainError
from transbridge.application.projects.models import TransitionTarget
from transbridge.persistence.v2.project_activation_journal import _manifest_digest
from transbridge.persistence.v2.schema import serialize_document

from . import test_workflow_closure_audit as audit

service_factory = audit.service_factory
_new_project = audit._new_project
_open_current = audit._open_current


class ProcessInterrupted(BaseException):
    pass


def _formal_files(root):
    paths = [*root.glob("projects/**/*.json"), root / "active-project.json"]
    return {str(path.relative_to(root)): path.read_bytes() for path in paths if path.exists()}


def _operation(factory, operation):
    services, context = _new_project(factory)
    original = services.project_lifecycle.active.formal_variant_ref
    if operation != "create":
        assert services.gui_project_commands.create_variant("Branch", context).is_success
    active = services.project_lifecycle.active
    if operation == "create":
        action = partial(services.gui_project_commands.create_variant, "New", context)
    elif operation == "switch":
        action = partial(services.gui_project_commands.switch_v2, active.project_ref, original, context)
    else:
        action = partial(
            services.gui_project_commands.delete_variant, active.formal_variant_ref.identity.value, context
        )
    return services, context, action


def _intercept(filesystem, monkeypatch, stage, error, *, after=True):
    original = filesystem.replace_durable
    fired = []

    def publish(source, destination):
        source_path, target_path = Path(source), Path(destination)
        is_marker = target_path.parent.name == "project-activation-journal"
        phase = json.loads(Path(source).read_bytes())["phase"] if is_marker else None
        matches = {
            "project": target_path.parent.name == "projects",
            "variant": target_path.parent.name == "variants" or source_path.parent.name == "variants",
            "pointer": target_path.name == "active-project.json",
            "prepared": is_marker and phase == "prepared",
            "committed": is_marker and phase == "committed",
        }[stage]
        if matches and not fired:
            fired.append(True)
            if after:
                original(source, destination)
            raise error("injected publication interruption")
        return original(source, destination)

    monkeypatch.setattr(filesystem, "replace_durable", publish)
    return fired


@pytest.mark.parametrize("operation", ["create", "switch", "delete"])
@pytest.mark.parametrize("stage", ["prepared", "project", "pointer", "committed"])
def test_interrupted_catalog_operation_recovers_old_or_committed_state(
    service_factory, tmp_path, monkeypatch, operation, stage
):
    services, context, action = _operation(service_factory, operation)
    before = _formal_files(tmp_path)
    with monkeypatch.context() as patch:
        fired = _intercept(services.filesystem, patch, stage, ProcessInterrupted)
        with pytest.raises(ProcessInterrupted):
            action()
        assert fired
    after_crash = _formal_files(tmp_path)
    restarted = _open_current(service_factory, context)
    expected = after_crash if stage == "committed" else before
    assert _formal_files(tmp_path) == expected
    assert not tuple((tmp_path / "project-activation-journal").glob("*.json"))
    _open_current(service_factory, context)
    assert _formal_files(tmp_path) == expected
    active = restarted.project_lifecycle.active
    assert active.project.envelope.data["active_variant_id"] == active.formal_variant_ref.identity.value


@pytest.mark.parametrize("operation", ["create", "delete"])
def test_interruption_after_variant_creation_or_removal_is_recoverable(
    service_factory, tmp_path, monkeypatch, operation
):
    services, context, action = _operation(service_factory, operation)
    before = _formal_files(tmp_path)
    with monkeypatch.context() as patch:
        _intercept(services.filesystem, patch, "variant", ProcessInterrupted)
        with pytest.raises(ProcessInterrupted):
            action()
    _open_current(service_factory, context)
    assert _formal_files(tmp_path) == before


@pytest.mark.parametrize("stage", ["prepared", "project", "variant", "pointer", "committed"])
@pytest.mark.parametrize("after", [False, True])
def test_publication_io_failure_rolls_back_unless_commit_marker_was_published(
    service_factory, tmp_path, monkeypatch, stage, after
):
    services, _context, action = _operation(service_factory, "create")
    before = _formal_files(tmp_path)
    original_active = services.project_lifecycle.active
    with monkeypatch.context() as patch:
        fired = _intercept(services.filesystem, patch, stage, OSError, after=after)
        result = action()
    assert fired
    if stage == "committed" and after:
        assert result.is_success, result.diagnostics
        assert _formal_files(tmp_path) != before
    else:
        assert not result.is_success
        assert _formal_files(tmp_path) == before
        assert services.project_lifecycle.active is original_active
    assert not tuple((tmp_path / "project-activation-journal").glob("*.json"))


def test_unknown_external_edit_fences_recovery_and_subsequent_repository_writes(service_factory, tmp_path, monkeypatch):
    services, _context, action = _operation(service_factory, "create")
    active = services.project_lifecycle.active
    with monkeypatch.context() as patch:
        _intercept(services.filesystem, patch, "project", ProcessInterrupted)
        with pytest.raises(ProcessInterrupted):
            action()
    path = Path(services.projects.path_for(active.project_ref))
    changed = json.loads(path.read_bytes())
    changed["revision"] += 10
    changed["data"]["name"] = "External edit"
    path.write_bytes(serialize_document(changed))
    before = _formal_files(tmp_path)
    with pytest.raises(DomainError, match="newer content"):
        service_factory()
    with pytest.raises(DomainError, match="newer content"):
        services.projects.save(active.project_ref, active.project)
    assert _formal_files(tmp_path) == before
    assert tuple((tmp_path / "project-activation-journal").glob("*.json"))


@pytest.mark.parametrize("corruption", ["shape", "extra", "foreign", "pointer", "duplicate"])
def test_checksum_valid_but_invalid_manifest_is_rejected_without_writes(
    service_factory, tmp_path, monkeypatch, corruption
):
    services, _context, action = _operation(service_factory, "create")
    with monkeypatch.context() as patch:
        _intercept(services.filesystem, patch, "project", ProcessInterrupted)
        with pytest.raises(ProcessInterrupted):
            action()
    path = next((tmp_path / "project-activation-journal").glob("*.json"))
    manifest = json.loads(path.read_bytes())
    if corruption == "shape":
        manifest["records"] = {"unexpected": "mapping"}
    elif corruption == "extra":
        manifest["records"][0]["unexpected"] = True
    elif corruption == "foreign":
        record = next(record for record in manifest["records"] if record["kind"] == "variant")
        record["project_id"] = "foreign"
    elif corruption == "pointer":
        record = next(record for record in manifest["records"] if record["kind"] == "pointer")
        record["target"] = None
        record["target_digest"] = None
    else:
        manifest["records"].append(manifest["records"][0])
    manifest["digest"] = _manifest_digest(manifest)
    path.write_bytes(serialize_document(manifest))
    before = _formal_files(tmp_path)
    with pytest.raises(DomainError, match="Invalid activation journal"):
        service_factory()
    assert _formal_files(tmp_path) == before
    assert path.exists()


def test_prepared_switch_rejects_same_revision_variant_content_change(service_factory):
    services, context, _action = _operation(service_factory, "switch")
    active = services.project_lifecycle.active
    prepared = services.project_lifecycle.prepare_transition(
        TransitionTarget(active.project_ref, active.formal_variant_ref), context
    )
    assert prepared.is_success
    stored = services.variants.read_snapshot(active.formal_variant_ref)
    changed = replace(stored.envelope, data={**stored.envelope.data, "label_library": {"new": {"name": "New"}}})
    services.variants.save(active.formal_variant_ref, replace(stored, envelope=changed))
    result = services.project_lifecycle.commit_transition(prepared.value["token"], context)
    assert not result.is_success
    assert result.diagnostics[0].code == "PROJECT_ACTIVATION_STALE"


@pytest.mark.parametrize("entity", ["project", "variant"])
def test_catalog_preparation_rejects_active_content_changed_without_revision(service_factory, entity, tmp_path):
    services, context = _new_project(service_factory)
    active = services.project_lifecycle.active
    if entity == "project":
        stored = services.projects.read_snapshot(active.project_ref)
        changed = replace(stored.envelope, data={**stored.envelope.data, "name": "External same-revision rename"})
        services.projects.save(active.project_ref, replace(stored, envelope=changed))
    else:
        stored = services.variants.read_snapshot(active.formal_variant_ref)
        changed = replace(stored.envelope, data={**stored.envelope.data, "label_library": {"new": {"name": "New"}}})
        services.variants.save(active.formal_variant_ref, replace(stored, envelope=changed))
    before = _formal_files(tmp_path)
    result = services.gui_project_commands.create_variant("Must not overwrite", context, copy_active=True)
    assert not result.is_success
    assert result.diagnostics[0].code == "PROJECT_CATALOG_STALE"
    assert _formal_files(tmp_path) == before


def test_root_lock_preserves_nonblocking_and_read_then_write_recovery(service_factory):
    services, _context = _new_project(service_factory)
    lock = services.projects.mutation_lock
    calls = []

    class Recovery:
        def run(self):
            calls.append("recovered")

    recovery = Recovery()
    lock.add_recovery(recovery.run)
    with lock.reading():
        assert calls == []
        with lock:
            assert calls == ["recovered"]
            assert lock.acquire(blocking=False)
            lock.release()
            acquired = []

            def probe():
                acquired.append(lock.acquire(blocking=False))

            thread = Thread(target=probe)
            thread.start()
            thread.join(5)
            assert acquired == [False]


def test_second_process_cannot_write_while_root_publication_lease_is_held(service_factory, tmp_path):
    services, _context = _new_project(service_factory)
    script = """
import sys
from transbridge.persistence.v2 import OsPersistenceFilesystem, ProjectRepository
from transbridge.persistence.v2.models import ReadOnlyWriteRefused
repository = ProjectRepository(sys.argv[1], OsPersistenceFilesystem())
try:
    with repository.mutation_lock:
        print('acquired')
except ReadOnlyWriteRefused as exc:
    print(exc.code)
"""
    with services.projects.mutation_lock:
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "PROJECT_WRITER_BUSY"
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "acquired"


def test_catching_nested_recovery_error_does_not_allow_later_write_in_same_read_lease(service_factory):
    services, _context = _new_project(service_factory)
    lock = services.projects.mutation_lock
    calls = []

    class BrokenRecovery:
        def run(self):
            calls.append("blocked")
            raise RuntimeError("recovery unresolved")

    recovery = BrokenRecovery()
    lock.add_recovery(recovery.run)
    with lock.reading():
        for _ in range(2):
            with pytest.raises(RuntimeError, match="recovery unresolved"), lock:
                pytest.fail("unresolved recovery must never admit a writer")
    assert calls == ["blocked", "blocked"]


@pytest.mark.parametrize("failure", ["staging_write", "staging_read", "record_readback"])
def test_staging_and_readback_faults_restore_exact_preimages(service_factory, tmp_path, monkeypatch, failure):
    services, _context, action = _operation(service_factory, "create")
    before = _formal_files(tmp_path)
    fs = services.filesystem
    write, read, publish = fs.write_bytes, fs.read_bytes, fs.replace_durable
    armed = set()
    fired = []

    def write_stage(path, payload):
        document = json.loads(payload)
        if document.get("entity_type") == "project":
            armed.add(str(path))
            if failure == "staging_write" and not fired:
                fired.append(True)
                raise OSError("injected staging write failure")
        return write(path, payload)

    def read_record(path):
        matches = (failure == "staging_read" and str(path) in armed) or (
            failure == "record_readback" and str(path) in armed and Path(path).parent.name == "projects"
        )
        if matches and not fired:
            fired.append(True)
            raise OSError("injected verification read failure")
        return read(path)

    def publish_record(source, destination):
        publish(source, destination)
        if Path(destination).parent.name == "projects":
            armed.add(str(destination))

    with monkeypatch.context() as patch:
        patch.setattr(fs, "write_bytes", write_stage)
        patch.setattr(fs, "read_bytes", read_record)
        patch.setattr(fs, "replace_durable", publish_record)
        result = action()
    assert fired
    assert not result.is_success
    assert _formal_files(tmp_path) == before


def test_baseline_registration_failure_rolls_back_files_and_registry(service_factory, tmp_path, monkeypatch):
    services, _context, action = _operation(service_factory, "create")
    before = _formal_files(tmp_path)
    active = services.project_lifecycle.active
    original = services.baselines.register
    added = []

    def fail_after_registration(project_ref, variant_ref, baselines, *, allow_empty=False):
        original(project_ref, variant_ref, baselines, allow_empty=allow_empty)
        added.append(variant_ref)
        raise RuntimeError("injected baseline registration failure")

    with monkeypatch.context() as patch:
        patch.setattr(services.baselines, "register", fail_after_registration)
        result = action()
    assert not result.is_success
    assert len(added) == 1
    assert services.baselines.capture_catalog(active.project_ref, tuple(added)) == ((added[0], None),)
    assert _formal_files(tmp_path) == before


def test_committed_cleanup_failure_reports_success_but_blocks_new_writes_until_cleanup(
    service_factory, tmp_path, monkeypatch
):
    services, _context, action = _operation(service_factory, "create")
    original = services.filesystem.replace_durable

    def fail_cleanup(source, destination):
        if Path(source).parent.name == "project-activation-journal" and Path(destination).suffix == ".removed":
            raise OSError("injected journal cleanup failure")
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(services.filesystem, "replace_durable", fail_cleanup)
        result = action()
        assert result.is_success, result.diagnostics
        active = services.project_lifecycle.active
        before_retry = _formal_files(tmp_path)
        with pytest.raises(OSError, match="journal cleanup failure"):
            services.projects.save(active.project_ref, active.project)
        assert _formal_files(tmp_path) == before_retry
    services.projects.save(active.project_ref, active.project)
    assert _formal_files(tmp_path) == before_retry
    assert not tuple((tmp_path / "project-activation-journal").glob("*.json"))


def test_failed_rollback_retains_journal_and_restart_completes_recovery(service_factory, tmp_path, monkeypatch):
    services, context, action = _operation(service_factory, "create")
    before = _formal_files(tmp_path)
    original = services.filesystem.replace_durable
    project_publishes = []

    def fail_pointer_and_rollback(source, destination):
        path = Path(destination)
        if path.name == "active-project.json":
            raise OSError("injected pointer failure")
        if path.parent.name == "projects":
            project_publishes.append(str(path))
            if len(project_publishes) > 1:
                raise OSError("injected rollback failure")
        return original(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(services.filesystem, "replace_durable", fail_pointer_and_rollback)
        result = action()
    assert not result.is_success
    assert tuple((tmp_path / "project-activation-journal").glob("*.json"))
    _open_current(service_factory, context)
    assert _formal_files(tmp_path) == before


def test_rollback_removes_pointer_that_was_absent_before_publication(service_factory, tmp_path, monkeypatch):
    services, context, action = _operation(service_factory, "create")
    (tmp_path / "active-project.json").unlink()
    before = _formal_files(tmp_path)
    with monkeypatch.context() as patch:
        _intercept(services.filesystem, patch, "pointer", ProcessInterrupted)
        with pytest.raises(ProcessInterrupted):
            action()
    restarted = service_factory()
    assert _formal_files(tmp_path) == before
    result = restarted.current_project_opener.prepare_active(context)
    assert not result.is_success
    assert result.diagnostics[0].code == "RESOURCE_NOT_FOUND"


def test_deleting_inactive_variant_keeps_retained_variant_bytes_and_pointer(service_factory, tmp_path):
    services, context = _new_project(service_factory)
    removed_ref = services.project_lifecycle.active.formal_variant_ref
    assert services.gui_project_commands.create_variant("Retained", context).is_success
    active = services.project_lifecycle.active
    retained_path = Path(services.variants.path_for(active.formal_variant_ref))
    retained_before = retained_path.read_bytes()
    pointer_before = (tmp_path / "active-project.json").read_bytes()
    result = services.gui_project_commands.delete_variant(removed_ref.identity.value, context)
    assert result.is_success, result.diagnostics
    assert not Path(services.variants.path_for(removed_ref)).exists()
    assert retained_path.read_bytes() == retained_before
    assert (tmp_path / "active-project.json").read_bytes() == pointer_before
    assert services.baselines.capture_catalog(active.project_ref, (removed_ref,)) == ((removed_ref, None),)
    assert services.project_lifecycle.active.formal_variant_ref == active.formal_variant_ref
