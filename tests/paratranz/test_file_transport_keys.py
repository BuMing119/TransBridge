"""File API publication and downloads use the same transport identity codec."""

from dataclasses import replace
from email.parser import BytesParser
import json

import pytest

from tests.contracts.io.test_paratranz_transport_keys import NS, qualified
from tests.paratranz.test_file_publication import gateway
from transbridge.application.categorized_upload.models import UploadSource
from transbridge.application.file_upload import execute_file_upload, upload_files, wire_records
from transbridge.application.io.identity import EntryRevision
from transbridge.application.io.paratranz_keys import ParaTranzKeyError
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.application.sync import (
    ConflictPolicy,
    LocalEntrySnapshot,
    SyncAction,
    SyncOperation,
    SyncPlanner,
)
from transbridge.application.sync.local_candidate import RemoteSourceIndex, build_local_candidate
from transbridge.paratranz.batch_download_snapshot import BatchDownloadScope, BatchDownloadSnapshotReader
from transbridge.paratranz.sync_snapshot import ParaTranzRemoteSnapshotAdapter, _snapshot


def download(port, *paths):
    return BatchDownloadSnapshotReader(port.config, gateway_factory=lambda _config: port).fetch(
        7, (BatchDownloadScope("a", NS, paths),), limit=100
    )[0]


def plan(local, remote):
    return SyncPlanner(local_state_only=True, preserve_local_source=True).plan(
        local,
        remote,
        operation=SyncOperation.DOWNLOAD,
        scope=f"paratranz:project:7:source:{NS.value}",
        conflict_policy=ConflictPolicy.PREFER_REMOTE,
    )


def files(*, categorized=True, entries=None):
    sources = (UploadSource("a", "A.esp", entries or (qualified("One"), qualified("Two"))),)
    return upload_files(sources, categorized=categorized)


def multipart_records(request):
    message = BytesParser().parsebytes(
        ("Content-Type: " + request.headers["Content-Type"] + "\r\n\r\n").encode() + request.body
    )
    return next(json.loads(part.get_payload(decode=True)) for part in message.get_payload() if part.get_filename())


@pytest.mark.parametrize("categorized", [False, True])
def test_conflicts_stay_in_one_file_and_one_file_import(categorized):
    artifacts = files(categorized=categorized)
    assert len(artifacts) == 1 and "冲突" not in artifacts[0].path
    port, session = gateway([], {"file": {"id": 20}}, {"file": {"id": 20}})
    receipt = execute_file_upload(7, artifacts, port, mode="both")
    assert receipt.succeeded == 1 and len(session.calls) == 3
    source = multipart_records(session.calls[1])
    translations = json.loads(session.calls[2].body)["items"]
    assert len({row["key"] for row in source}) == 2
    assert [row["key"] for row in translations] == [row["key"] for row in source]
    assert [row["original"] for row in source] == ["One", "Two"]
    assert all("id" not in row for row in source)


def test_selection_order_and_layout_do_not_change_transport_keys():
    entries = (qualified("One"), qualified("Two"))
    expected = {row["original"]: row["key"] for row in wire_records(files()[0])}
    for subset in (entries[:1], entries[1:], tuple(reversed(entries))):
        for categorized in (True, False):
            rows = wire_records(files(categorized=categorized, entries=subset)[0])
            assert all(row["key"] == expected[row["original"]] for row in rows)


def test_identical_local_identity_is_still_rejected_without_post():
    artifact = files(entries=(qualified(), qualified()))[0]
    port, session = gateway([])
    receipt = execute_file_upload(7, (artifact,), port)
    assert receipt.failed == 1 and len(session.calls) == 1


def test_413_splitting_preserves_encoded_keys():
    artifacts = files()
    expected = wire_records(artifacts[0])
    port, session = gateway([], (413, {}, {}), {"file": {"id": 20}}, {"file": {"id": 21}})
    receipt = execute_file_upload(7, artifacts, port)
    assert receipt.failed == 0
    actual = [row for request in session.calls[2:] for row in multipart_records(request)]
    assert actual == list(expected)


def test_online_download_and_execution_restore_translations_to_exact_local_originals():
    artifact = files()[0]
    rows = [dict(row, id=index + 20) for index, row in enumerate(wire_records(artifact))]
    port, session = gateway([{"id": 5, "name": artifact.path}], rows)
    read = download(port, "A.esp.json", artifact.path)
    assert not read.error
    remote = read.entries
    local = tuple(
        LocalEntrySnapshot(entry.identity, EntryRevision(1), entry.original, "", "QUST:CNAM", 0)
        for entry in artifact.entries
    )
    preview = plan(local, remote)
    assert all(item.action is SyncAction.UPDATE_LOCAL for item in preview.items)
    updated = build_local_candidate(
        local, RemoteSourceIndex(remote), preview.items, attach_remote_refs=False, preserve_local_source=True
    )
    assert {item.entry_key: item.translation for item in updated} == {
        entry.identity: entry.translation for entry in artifact.entries
    }
    assert all(item.external_ref is None for item in updated)
    assert len(session.calls) == 2


def test_legacy_and_encoded_candidates_with_different_translations_require_review():
    entry = qualified()
    encoded = wire_records(files(entries=(entry,))[0])[0]
    new = _snapshot(ParaTranzEntry.from_mapping(dict(encoded, id=20)), NS, "project:7", decode_keys=True)
    old = _snapshot(
        ParaTranzEntry.from_mapping(dict(encoded, key=entry.key, id=21, translation="旧云端译文")),
        NS,
        "project:7",
        decode_keys=True,
    )
    current = LocalEntrySnapshot(entry.identity, EntryRevision(1), entry.original, "", "QUST:CNAM", 0)
    preview = plan((current,), (new, old))
    assert preview.items[0].action is SyncAction.CONFLICT
    assert len(preview.download_review) == 1
    equivalent = plan((current,), (new, replace(old, translation=new.translation)))
    assert equivalent.items[0].action is SyncAction.UPDATE_LOCAL


def test_download_adapter_decodes_but_legacy_upload_snapshot_keeps_real_remote_key():
    row = ParaTranzEntry.from_mapping(wire_records(files()[0])[0])

    class Service:
        def list_entries(self, *args, **kwargs):
            return (row,)

    legacy = ParaTranzRemoteSnapshotAdapter(Service()).fetch(7, NS, limit=10)
    downloaded = ParaTranzRemoteSnapshotAdapter(Service(), decode_keys=True).fetch(7, NS, limit=10)
    assert legacy[0].entry_key.local_key == row.key
    assert downloaded[0].entry_key == qualified().identity
    with pytest.raises(ParaTranzKeyError):
        _snapshot(replace(row, original="Changed"), NS, "project:7", decode_keys=True)


def test_old_conflict_files_are_excluded_without_reading_their_contents():
    entries = (qualified("One"), qualified("Two"))
    port, session = gateway(
        [{"id": index + 5, "name": f"A.esp/任务/任务日志__冲突_{index}.json"} for index in range(2)],
    )
    read = download(port, "A.esp.json", files()[0].path)
    assert not read.error
    remote = read.entries
    local = tuple(
        LocalEntrySnapshot(entry.identity, EntryRevision(), entry.original, "", "QUST:CNAM", 0) for entry in entries
    )
    preview = plan(local, remote)
    assert remote == ()
    assert all(item.action is not SyncAction.UPDATE_LOCAL for item in preview.items)
    assert len(session.calls) == 1


def test_changed_cloud_original_fails_download_without_further_requests():
    row = dict(wire_records(files()[0])[0], original="Changed remotely")
    port, session = gateway([{"id": 5, "name": "A.esp.json"}], [row])
    read = download(port, "A.esp.json")
    assert read.error and "哈希" in read.error
    assert read.entries == ()
    assert len(session.calls) == 2


def test_thousand_qualified_siblings_still_use_one_file_request():
    artifacts = files(entries=tuple(qualified(str(index)) for index in range(1000)))
    port, session = gateway([], {"file": {"id": 20}})
    result = execute_file_upload(7, artifacts, port)
    assert result.succeeded == 1 and len(session.calls) == 2
    assert len({row["key"] for row in multipart_records(session.calls[1])}) == 1000
