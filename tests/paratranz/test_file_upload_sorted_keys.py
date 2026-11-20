from contextlib import contextmanager
import json
from zipfile import ZipFile

import pytest

from tests.paratranz.test_file_publication import artifact, gateway
from transbridge.application.file_upload import execute_file_upload, wire_records
from transbridge.application.io.identity import SourceNamespace
from transbridge.application.ports.paratranz import ParaTranzEntry
from transbridge.paratranz.artifact_file_reads import ARTIFACT_FILE_THRESHOLD, ArtifactFileReads
from transbridge.paratranz.sync_snapshot import _snapshot


def _legacy(file, file_id, **fields):
    (record,) = wire_records(file)
    return [{**record, "key": "0", "id": 10 * file_id, "file": file_id, **fields}]


@pytest.mark.parametrize("mode", ["orig_only", "both"])
def test_files_with_only_order_changes_share_one_id_based_request(mode):
    a, b = artifact(path="a.json"), artifact(path="b.json")
    listing = [{"name": "a.json", "id": 1}, {"name": "b.json", "id": 2}]
    port, session = gateway(listing, _legacy(a, 1), _legacy(b, 2), [20, 10])
    result = execute_file_upload(7, (a, b), port, mode=mode)
    assert result.succeeded == 2 and not result.retry_files
    assert all(row.source_applied and not row.translation_applied for row in result.files)
    assert [call.method for call in session.calls] == ["GET", "GET", "GET", "PUT"]
    body = json.loads(session.calls[-1].body)
    assert body == {"op": "edit", "items": [{"id": i, "key": "00000000|TB2|0", "stage": 3} for i in (10, 20)]}


def test_partial_rename_acknowledgement_preserves_file_results_and_retry_scope():
    a, b = artifact(path="a.json"), artifact(path="b.json")
    port, session = gateway(
        [{"name": "a.json", "id": 1}, {"name": "b.json", "id": 2}], _legacy(a, 1), _legacy(b, 2), [10]
    )
    result = execute_file_upload(7, (a, b), port)
    assert [row.status for row in result.files] == ["succeeded", "uncertain"]
    assert result.retry_files == (b,)
    assert [call.method for call in session.calls].count("PUT") == 1


def test_rejected_batch_rename_refreshes_inventory_once_and_compares_replacements():
    a, b = artifact(path="a.json"), artifact(path="b.json")
    before = [{"name": "a.json", "id": 1}, {"name": "b.json", "id": 2}]
    after = [{"name": "a.json", "id": 3}, {"name": "b.json", "id": 4}]
    (replacement,) = wire_records(a)
    port, session = gateway(
        before,
        _legacy(a, 1),
        _legacy(b, 2),
        (404, {}, {}),
        after,
        [{**replacement, "translation": "旧"}],
        {"file": {"id": 3}},
        (404, {}, {}),
    )
    result = execute_file_upload(7, (a, b), port, mode="both")
    assert [row.status for row in result.files] == ["succeeded", "failed"]
    assert result.retry_files == (b,)
    assert [(req.method, req.path_url.removeprefix("/api/projects/7/")) for req in session.calls] == [
        ("GET", "files"),
        ("GET", "files/1/translation"),
        ("GET", "files/2/translation"),
        ("PUT", "strings"),
        ("GET", "files"),
        ("GET", "files/3/translation"),
        ("POST", "files/3/translation"),
        ("GET", "files/4/translation"),
    ]


def test_size_rejected_rename_splits_only_the_string_edit():
    port, session = gateway((413, {}, {}), [10], [20])
    port.rename_keys(7, ({"id": 10, "key": "a", "stage": 0}, {"id": 20, "key": "b", "stage": 9}))
    assert [call.method for call in session.calls] == ["PUT"] * 3
    assert [[row["id"] for row in json.loads(call.body)["items"]] for call in session.calls] == [[10, 20], [10], [20]]


def test_unconfirmed_rename_stops_both_before_source_and_translation():
    file = artifact()
    port, session = gateway([{"id": 1, "name": file.path}], _legacy(file, 1, original="old", translation="旧"), [])
    result = execute_file_upload(7, (file,), port, mode="both")
    assert result.files[0].status == "uncertain"
    assert result.retry_files == (file,)
    assert [req.method for req in session.calls] == ["GET", "GET", "PUT"]


@pytest.mark.parametrize(
    ("mode", "missing"),
    [("orig_only", "id"), ("trans_safe", "stage"), ("both", "translation"), ("trans_force", "translation")],
)
def test_archive_with_one_incomplete_member_only_refetches_that_file(mode, missing, monkeypatch):
    files = tuple(artifact(path=f"A.esp/{i}.json") for i in range(ARTIFACT_FILE_THRESHOLD))
    listing = [{"id": i, "name": file.path} for i, file in enumerate(files, 1)]
    port, session = gateway(listing, list(wire_records(files[0])))
    downloads = []

    class LocalDownload:
        def download(self, project_id, path, **kwargs):
            downloads.append(path)
            with ZipFile(path, "w") as archive:
                for i, file in enumerate(files):
                    (row,) = wire_records(file)
                    if i == 0:
                        if missing == "id":
                            row["key"] = "0"
                        else:
                            del row[missing]
                    archive.writestr(f"raw/{file.path}.json", json.dumps([row]))

    monkeypatch.setattr(
        "transbridge.paratranz.file_publication.ArtifactFileReads",
        lambda config: ArtifactFileReads(config, downloader_factory=lambda _: LocalDownload()),
    )
    result = execute_file_upload(7, files, port, mode=mode)
    assert all(row.status == "unchanged" for row in result.files)
    assert len(downloads) == 1 and not downloads[0].exists()
    assert [(req.method, req.path_url) for req in session.calls] == [
        ("GET", "/api/projects/7/files"),
        ("GET", "/api/projects/7/files/1/translation"),
    ]


@pytest.mark.parametrize("mode", ["orig_only", "both", "trans_safe", "trans_force"])
def test_three_changed_files_out_of_a_hundred_send_three_writes(mode, monkeypatch):
    files = tuple(artifact(path=f"{i}.json") for i in range(1, 101))
    listing = [{"id": i, "name": file.path} for i, file in enumerate(files, 1)]
    port, session = gateway(listing, *({"file": {"id": i}} for i in (11, 52, 84)))
    selected = []
    reads = []

    @contextmanager
    def snapshot(project_id, inventory, selected_ids, **kwargs):
        selected.extend(selected_ids)

        def read(project_id, file_id):
            reads.append(file_id)
            (row,) = wire_records(files[file_id - 1])
            if file_id in {11, 52, 84}:
                field = "original" if mode == "orig_only" else "translation"
                row[field] = "old text"
            return [row]

        yield read

    monkeypatch.setattr(port, "force_comparison", snapshot)
    result = execute_file_upload(7, files, port, mode=mode)
    assert result.succeeded == 100
    assert len(selected) == len(reads) == 100
    assert [row.path for row in result.files if row.status == "succeeded"] == ["11.json", "52.json", "84.json"]
    assert [call.method for call in session.calls] == ["GET", "POST", "POST", "POST"]


def test_workbench_download_decodes_order_without_dropping_remote_identity():
    namespace = SourceNamespace("source:sample")
    remote = ParaTranzEntry(42, "00000428|TB2|A", "original", "译文", "BOOK:FULL", 5)
    row = _snapshot(remote, namespace, "project:1", decode_keys=True)
    assert (row.entry_key.local_key, row.context_order, row.context, row.external_ref.opaque_id) == (
        "A",
        428,
        "BOOK:FULL",
        42,
    )
