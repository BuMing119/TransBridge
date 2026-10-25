"""Verify the ordinary boundary against offline multipart/readback behavior."""

from types import SimpleNamespace

import pytest

from transbridge.application.categorized_upload import RemoteEntry
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.ports.paratranz import ExternalServiceError, ParaTranzEntry
from transbridge.paratranz.ordinary_upload import OrdinaryUploadAdapter

from .ordinary_upload_support import ImportSession, gateway


def _adapter(session):
    result = OrdinaryUploadAdapter(SimpleNamespace(close=lambda: None), gateway(session), "A.esp.json")
    result.fetch(7, SourceNamespace("a"), limit=100)
    return result


def _local(*, key="new", stage=5):
    return ParaTranzEntry(None, key, "Original", "译文", "00000000|NPC_:FULL", stage)


@pytest.mark.parametrize("stage", [1, 5])
def test_first_file_import_normalizes_translation_state_before_reporting_success(stage):
    session = ImportSession(import_stage=0)
    adapter = _adapter(session)
    result = adapter.upsert_entry(7, _local(stage=stage))
    assert (result.translation, result.stage) == ("译文", stage)
    assert (session.rows[result.remote_id]["translation"], session.rows[result.remote_id]["stage"]) == ("译文", stage)
    assert len(session.imports) == 1 and session.imports[0][0:2] == ("", "A.esp.json")


def test_lost_first_import_response_is_not_reposted_and_next_snapshot_recovers_identity():
    session = ImportSession(lose_response=True)
    adapter = _adapter(session)
    with pytest.raises(ExternalServiceError):
        adapter.upsert_entry(7, _local())
    assert len(session.imports) == 1 and len(session.rows) == 1
    known_id = next(iter(session.rows))
    recovered = adapter.fetch(7, SourceNamespace("a"), limit=100)
    assert len(recovered) == 1 and recovered[0].external_ref.opaque_id == known_id
    assert len(session.imports) == 1


def test_new_entry_cannot_reuse_an_existing_remote_identity(monkeypatch):
    session = ImportSession()
    session.files[1] = {"id": 1, "name": "A.esp.json"}
    old = {
        "id": 11,
        "fileId": 1,
        "key": "existing",
        "original": "Old",
        "translation": "旧",
        "context": "Old",
        "stage": 1,
    }
    session.rows[11] = dict(old)
    adapter = _adapter(session)

    def alias(_project_id, file_id, entry):
        return RemoteEntry(11, file_id, entry.key, entry.original, entry.translation, entry.context, entry.stage)

    monkeypatch.setattr(adapter.gateway, "create_entry", alias)
    with pytest.raises(ValueError):
        adapter.upsert_entry(7, _local())
    assert session.rows[11] == old
    assert adapter._entries[11].key == "existing"


def test_path_rename_during_entry_read_prevents_updating_the_moved_file(monkeypatch):
    session = ImportSession()
    session.files[1] = {"id": 1, "name": "A.esp.json"}
    session.rows[11] = {
        "id": 11,
        "fileId": 1,
        "key": "existing",
        "original": "Old",
        "translation": "旧",
        "context": "Old",
        "stage": 1,
    }
    adapter = _adapter(session)
    read_entry = adapter.gateway.read_entry

    def rename(project_id, remote_id):
        session.files[1]["name"] = "Moved/A.esp.json"
        return read_entry(project_id, remote_id)

    monkeypatch.setattr(adapter.gateway, "read_entry", rename)
    with pytest.raises(RuntimeError):
        adapter.update_entry_translation(7, ParaTranzEntry(11, "existing", "Old", "新译文", "Old", 5))
    assert session.rows[11]["translation"] == "旧"
    assert all(call.method == "GET" for call in session.calls)
