from __future__ import annotations

import hashlib
import json

import pytest

from tests.application.sessions.test_gui_session_management import build_session_services
from tests.persistence.v2.fakes import MemoryFilesystem
from transbridge.application.contracts import RequestContext
from transbridge.persistence.v2.ids import SessionId, SessionRef


@pytest.fixture
def saved_sessions(tmp_path):
    filesystem = MemoryFilesystem()
    commands, lifecycle, repository, projection = build_session_services(tmp_path, filesystem)
    context = RequestContext("owner", project_id="project", variant_id="variant")
    refs = []
    for name in ("First", "Second"):
        assert commands.create_and_activate(name, context).is_success
        ref = lifecycle.active.aggregate.ref
        messages = [{"role": "user", "content": f"Content of {name}"}]
        assert commands.save_conversation(ref, messages, messages, context).is_success
        refs.append(ref)
    yield tmp_path, filesystem, commands, lifecycle, repository, tuple(refs)
    projection.close()


@pytest.mark.parametrize("damage", [None, b"{broken", b"[]", b'{"schema_version":1,"sessions":[{}]}'])
def test_catalog_rebuild_preserves_records_active_session_and_new_conversations(saved_sessions, damage):
    root, filesystem, commands, lifecycle, repository, refs = saved_sessions
    catalog_path = str(root / "session-catalog.json")
    original_records = {repository.path_for(ref): filesystem.read_bytes(repository.path_for(ref)) for ref in refs}
    active = lifecycle.active.aggregate.snapshot()
    if damage is None:
        filesystem.remove(catalog_path)
    else:
        filesystem.seed(catalog_path, damage)

    rows = commands.list_sessions()

    assert {row["name"] for row in rows} == {"First", "Second"}
    assert all(row["project_id"] == "project" and row["variant_id"] == "variant" for row in rows)
    assert all(row["available"] for row in rows)
    assert lifecycle.active.aggregate.snapshot() == active
    assert all(filesystem.read_bytes(path) == raw for path, raw in original_records.items())
    if damage is not None:
        backup = root / "backups" / "session-catalog" / f"{hashlib.sha256(damage).hexdigest()}.json"
        assert filesystem.read_bytes(str(backup)) == damage

    persisted = dict(filesystem.files)
    filesystem.calls.clear()
    assert commands.list_sessions() == rows
    assert filesystem.files == persisted
    assert not any(call[0] in {"write", "replace", "remove", "mkdir"} for call in filesystem.calls)
    assert commands.create_and_activate("New", RequestContext("owner")).is_success
    assert {row["name"] for row in commands.list_sessions()} == {"First", "Second", "New"}


def test_bad_record_is_preserved_and_does_not_hide_healthy_sessions(saved_sessions):
    root, filesystem, commands, _lifecycle, repository, refs = saved_sessions
    bad_path = repository.path_for(refs[0])
    filesystem.seed(bad_path, b"{bad record")
    filesystem.seed(str(root / "session-catalog.json"), b"{bad catalog")

    rows = {row["session_id"]: row for row in commands.list_sessions()}

    assert rows[refs[0].identity.value]["available"] is False
    assert rows[refs[1].identity.value]["available"] is True
    assert filesystem.read_bytes(bad_path) == b"{bad record"
    assert commands.create_and_activate("New", RequestContext("owner")).is_success


def test_missing_index_is_recovered_before_a_direct_create(saved_sessions):
    root, filesystem, commands, _lifecycle, _repository, _refs = saved_sessions
    filesystem.remove(str(root / "session-catalog.json"))
    assert commands.create_and_activate("New", RequestContext("owner")).is_success
    assert {row["name"] for row in commands.list_sessions()} == {"First", "Second", "New"}


def test_future_catalog_is_not_replaced(saved_sessions):
    root, filesystem, commands, _lifecycle, _repository, _refs = saved_sessions
    catalog_path = str(root / "session-catalog.json")
    future = b'{"schema_version":999,"sessions":[]}'
    filesystem.seed(catalog_path, future)
    with pytest.raises(RuntimeError, match="unsupported"):
        commands.list_sessions()
    assert filesystem.read_bytes(catalog_path) == future
    assert not any("backups" in path and "session-catalog" in path for path in filesystem.files)


@pytest.mark.parametrize("fault", ["scan", "backup", "publish"])
def test_recovery_failure_keeps_original_index_and_can_retry(saved_sessions, fault):
    root, filesystem, commands, _lifecycle, _repository, _refs = saved_sessions
    catalog_path = str(root / "session-catalog.json")
    damage = b"{broken"
    filesystem.seed(catalog_path, damage)
    if fault == "scan":
        filesystem.fail_list_paths.add(str(root / "sessions"))
    elif fault == "backup":
        filesystem.fail_write_paths.add(
            str(root / ".staging" / f"catalog-backup-{hashlib.sha256(damage).hexdigest()}.tmp")
        )
    else:
        filesystem.fail_replace_destinations.add(catalog_path)
    with pytest.raises(OSError):
        commands.list_sessions()
    assert filesystem.read_bytes(catalog_path) == damage
    filesystem.fail_list_paths.clear()
    filesystem.fail_write_paths.clear()
    filesystem.fail_replace_destinations.clear()
    assert len(commands.list_sessions()) == 2


def test_recovery_never_adopts_noncanonical_or_escaping_records(saved_sessions):
    root, filesystem, commands, _lifecycle, repository, refs = saved_sessions
    original = filesystem.read_bytes(repository.path_for(refs[0]))
    filesystem.seed(str(root / "sessions" / "random.json"), original)
    outside = root.parent / "outside.json"
    alias = root / "sessions" / f"{SessionId('alias').encoded}.json"
    filesystem.seed(str(alias), original)
    filesystem.canonical_aliases[str(alias)] = str(outside)
    filesystem.seed(str(outside), original)
    filesystem.remove(str(root / "session-catalog.json"))
    rows = commands.list_sessions()
    assert {row["session_id"] for row in rows} == {ref.identity.value for ref in refs}
    assert filesystem.read_bytes(str(outside)) == original


def test_repaired_record_becomes_available_without_rebuilding_again(saved_sessions):
    root, filesystem, commands, _lifecycle, repository, refs = saved_sessions
    path = repository.path_for(refs[0])
    original = filesystem.read_bytes(path)
    filesystem.seed(path, b"{broken")
    filesystem.remove(str(root / "session-catalog.json"))
    assert any(not row["available"] for row in commands.list_sessions())
    filesystem.seed(path, original)
    assert all(row["available"] for row in commands.list_sessions())


def test_invalid_catalog_row_types_are_rebuilt(saved_sessions):
    root, filesystem, commands, _lifecycle, _repository, _refs = saved_sessions
    path = str(root / "session-catalog.json")
    document = json.loads(filesystem.read_bytes(path))
    document["sessions"][0]["name"] = None
    filesystem.seed(path, json.dumps(document).encode())
    assert {row["name"] for row in commands.list_sessions()} == {"First", "Second"}


def test_boolean_schema_is_rebuilt_instead_of_hiding_saved_sessions(saved_sessions):
    root, filesystem, commands, _lifecycle, _repository, _refs = saved_sessions
    filesystem.seed(str(root / "session-catalog.json"), b'{"schema_version":true,"sessions":[]}')
    assert {row["name"] for row in commands.list_sessions()} == {"First", "Second"}


def test_invalid_record_identity_is_only_listed_as_unavailable(saved_sessions):
    root, filesystem, commands, _lifecycle, repository, refs = saved_sessions
    path = repository.path_for(SessionRef(SessionId("other")))
    filesystem.seed(path, filesystem.read_bytes(repository.path_for(refs[0])))
    filesystem.remove(str(root / "session-catalog.json"))
    rows = {row["session_id"]: row for row in commands.list_sessions()}
    assert not rows["other"]["available"]
    assert rows["other"]["project_id"] is rows["other"]["variant_id"] is None
    assert all(rows[ref.identity.value]["available"] for ref in refs)


def test_aliases_of_the_same_record_are_recovered_once(saved_sessions):
    root, filesystem, commands, _lifecycle, repository, refs = saved_sessions
    path = repository.path_for(refs[0])
    alias = str(root / "sessions" / "alias.json")
    filesystem.seed(alias, filesystem.read_bytes(path))
    filesystem.canonical_aliases[alias] = path
    filesystem.remove(str(root / "session-catalog.json"))
    rows = commands.list_sessions()
    assert len(rows) == 2
    assert {row["session_id"] for row in rows} == {ref.identity.value for ref in refs}
    filesystem.calls.clear()
    assert commands.list_sessions() == rows
    assert not any(call[0] in {"write", "replace", "remove"} for call in filesystem.calls)
