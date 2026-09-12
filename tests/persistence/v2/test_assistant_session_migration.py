from __future__ import annotations

from copy import deepcopy
import json

import pytest

from transbridge.persistence.v2 import (
    LoadedRecord,
    ProjectId,
    ProjectRef,
    SessionId,
    SessionRef,
    SessionRepository,
    VariantId,
    VariantRef,
)
from transbridge.persistence.v2.filesystem import OsPersistenceFilesystem
from transbridge.persistence.v2.migration import migrate_to_current, migrate_v2_to_v3, migrate_v3_to_v4


def _session(version: int = 3) -> dict:
    return {
        "schema_version": version,
        "entity_type": "session",
        "id": "session-a",
        "revision": 7,
        "data": {
            "name": "Existing Session",
            "messages": [{"role": "user", "content": "translate"}],
            "history": [{"role": "assistant", "content": "confirm?"}],
            "project_id": None,
            "variant_id": None,
        },
    }


def test_session_migration_preserves_only_known_history_and_stable_ids(tmp_path) -> None:
    ref = SessionRef(SessionId("session-a"))
    fs = OsPersistenceFilesystem()
    repository = SessionRepository(str(tmp_path), fs)
    path = repository.path_for(ref)
    raw = json.dumps(_session()).encode()
    fs.make_dirs(str(tmp_path / "sessions"))
    fs.write_bytes(path, raw)

    loaded = repository.load(ref)

    assert isinstance(loaded, LoadedRecord)
    assert loaded.value.envelope.schema_version == 4
    assert loaded.migration_report.from_version == 3
    assert fs.read_bytes(loaded.migration_report.backup_path) == raw
    data = loaded.value.envelope.data
    assert data["messages"] == _session()["data"]["messages"]
    assert data["history"] == _session()["data"]["history"]
    state = data["assistant_state"]
    assert state["requests"] == []
    assert [item["projection"] for item in state["legacy_unassigned"]] == ["messages", "history"]
    assert all(item["origin"] == "legacy" and item["request_id"] is None for item in state["legacy_unassigned"])
    assert migrate_v3_to_v4(_session(), ref).document["data"] == data
    assert repository.load(ref).migrated is False


def test_migration_chain_runs_distinct_two_to_three_and_three_to_four_steps() -> None:
    ref = SessionRef(SessionId("session-a"))
    original = _session(2)
    before = deepcopy(original)
    intermediate = migrate_v2_to_v3(original, ref)
    assert intermediate.document["schema_version"] == 3
    assert "assistant_state" not in intermediate.document["data"]
    current = migrate_to_current(original, ref)
    assert current.document["schema_version"] == 4
    assert "assistant_state" in current.document["data"]
    assert original == before


@pytest.mark.parametrize(
    "ref", [ProjectRef(ProjectId("project-a")), VariantRef(VariantId("variant-a"), ProjectId("project-a"))]
)
def test_three_to_four_changes_only_envelope_for_project_and_variant(ref) -> None:
    document = {
        "schema_version": 3,
        "entity_type": ref.kind.value,
        "id": ref.identity.value,
        "revision": 8,
        "data": {"existing": {"nested": [1, "unchanged"]}},
    }
    original = deepcopy(document)
    draft = migrate_v3_to_v4(document, ref)
    assert draft.document == {**original, "schema_version": 4}
    assert document == original
