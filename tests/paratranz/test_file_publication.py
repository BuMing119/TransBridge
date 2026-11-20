"""File publication contracts, using prepared HTTP requests without live services."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event
from zipfile import ZipFile

import pytest
import requests

from transbridge.application.categorized_upload.models import CategorizedFile
from transbridge.application.file_upload import execute_file_upload, wire_records
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.artifact_file_reads import ARTIFACT_FILE_THRESHOLD, ArtifactFileReads
from transbridge.paratranz.file_publication import FilePublicationGateway, RequestBudget


class Responses(requests.Session):
    def __init__(self, responses):
        super().__init__()
        self.responses = deque(responses)
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        status, payload, headers = value if isinstance(value, tuple) else (200, value, {})
        response = requests.Response()
        response.status_code = status
        response.url, response.request = request.url, request
        response.headers.update(headers)
        response._content = json.dumps(payload).encode()
        return response


def gateway(*responses, cancel=None, budget=None, session_factory=None):
    session = Responses(responses)
    config = ParatranzConfig(
        token="file-publication-test-token",
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    port = FilePublicationGateway(
        config, session=session, cancel=cancel, budget=budget or RequestBudget(0), session_factory=session_factory
    )
    return port, session


def artifact(count=1, path="A.esp/人名.json"):
    return CategorizedFile(
        "a",
        "names",
        path,
        tuple(TranslationEntry(str(index), str(index), "Original", "译文", 3, "NPC_:FULL") for index in range(count)),
    )


def remote_rows(file, *, file_id=20, legacy=False, **changes):
    """A complete translation export, with independent server entry identities."""
    return [
        dict(
            row,
            id=index + 100,
            fileId=file_id,
            **({"key": entry.key} if legacy else {}),
            **changes,
        )
        for index, (entry, row) in enumerate(zip(file.entries, wire_records(file), strict=True))
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"files": []},
        {"files": [], "page": 1, "pageSize": 800, "pageCount": 0, "rowCount": 0},
        {"results": [], "page": 1, "pageCount": 1, "rowCount": 0},
        {"files": [{"id": 1, "name": "A.json"}]},
        {"files": [{"id": 1, "name": "A.json"}], "rowCount": 1},
        {"results": [{"id": 1, "name": "A.json"}], "page": 1, "pageSize": 1, "pageCount": 1, "rowCount": 1},
    ],
)
def test_inventory_accepts_complete_file_envelopes_without_string_reads(payload):
    port, session = gateway(payload)
    files = payload.get("files", payload.get("results"))
    assert port.inventory(7) == {file["name"]: file["id"] for file in files}
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "metadata",
    [
        {"page": True},
        {"page": 0},
        {"page": 2},
        {"page": None},
        {"rowCount": -1},
        {"rowCount": True},
        {"rowCount": "0"},
        {"rowCount": None},
        {"pageCount": -1},
        {"pageCount": True},
        {"pageCount": None},
        {"pageSize": 0},
        {"pageSize": False},
        {"pageSize": "1"},
        {"pageSize": None},
    ],
)
def test_inventory_rejects_invalid_pagination_without_writes(metadata):
    port, session = gateway({"files": [], **metadata})
    with pytest.raises(ValueError, match="云端"):
        port.inventory(7)
    assert [request.method for request in session.calls] == ["GET"]


@pytest.mark.parametrize(
    "files,metadata",
    [
        ([], {"rowCount": 1, "pageCount": 2}),
        ([], {"rowCount": 1}),
        ([], {"rowCount": 0, "pageCount": 2}),
        ([{"id": 1, "name": "A.json"}], {"rowCount": 1, "pageCount": 2}),
        ([{"id": 1, "name": "A.json"}], {"pageCount": 0}),
        ([{"id": 1, "name": "A.json"}], {"pageSize": 1}),
        ([{"id": 1, "name": "A.json"}], {"rowCount": 2, "pageCount": 1}),
        ([{"id": 1, "name": "A.json"}], {"rowCount": 0}),
        ([{"id": 1, "name": "A.json"}, {"id": 2, "name": "B.json"}], {"pageSize": 1, "rowCount": 2, "pageCount": 1}),
    ],
)
def test_inventory_rejects_incomplete_or_contradictory_listing_without_writes(files, metadata):
    port, session = gateway({"files": files, **metadata})
    with pytest.raises(ValueError, match="云端"):
        port.inventory(7)
    assert [request.method for request in session.calls] == ["GET"]


@pytest.mark.parametrize("count", [1, 1000, 10000])
def test_new_file_is_one_import_regardless_of_string_count(count):
    port, session = gateway([], {"file": {"id": 20}, "revision": {"id": 30}})
    result = execute_file_upload(7, (artifact(count),), port)
    assert result.succeeded == 1
    assert [(call.method, call.path_url) for call in session.calls] == [
        ("GET", "/api/projects/7/files"),
        ("POST", "/api/projects/7/files"),
    ]
    assert b'filename="' in session.calls[1].body
    assert "人名.json".encode() in session.calls[1].body
    assert b"A.esp/" in session.calls[1].body


def test_source_update_uses_incremental_and_server_hash_after_comparison():
    port, session = gateway(
        [{"id": 200, "name": "人名.json", "folder": "A.esp"}],
        remote_rows(artifact(), file_id=200, original="Previous source"),
        {"status": "hashMatched"},
    )
    result = execute_file_upload(7, (artifact(),), port)
    assert result.files[0].status == "unchanged"
    assert session.calls[1].path_url == "/api/projects/7/files/200/translation"
    request = session.calls[2]
    assert request.path_url == "/api/projects/7/files/200"
    assert b'name="incremental"\r\n\r\ntrue' in request.body
    assert b'name="skip"\r\n\r\nfalse' in request.body
    assert len(session.calls) == 3


@pytest.mark.parametrize("exact_exists", [False, True])
def test_source_update_prefers_exact_path_then_unique_case_insensitive_path(exact_exists):
    path = "BSHeartland.esm/任务/对话_[Aera the Devout's Missing Amulet].json"
    existing = [
        {"id": 20, "name": path.replace("Missing Amulet", "missing amulet")},
        {"id": 30, "name": path.replace("BSHeartland.esm", "Other.esm")},
    ]
    if exact_exists:
        existing.append({"id": 40, "name": path})
    file_id = 40 if exact_exists else 20
    port, session = gateway(
        existing,
        remote_rows(artifact(path=path), file_id=file_id, original="Previous source", translation="旧译文"),
        {"file": {"id": file_id}},
        {"status": "unchanged"},
    )

    result = execute_file_upload(7, (artifact(path=path),), port, mode="both")

    assert result.succeeded == 1
    assert [(call.method, call.path_url) for call in session.calls] == [
        ("GET", "/api/projects/7/files"),
        ("GET", f"/api/projects/7/files/{file_id}/translation"),
        ("POST", f"/api/projects/7/files/{file_id}"),
        ("POST", f"/api/projects/7/files/{file_id}/translation"),
    ]


def test_ambiguous_case_insensitive_path_fails_without_writing():
    file = artifact(path="A.esp/Quest.json")
    variants = ("A.esp/quest.json", "A.esp/QUEST.json")
    port, session = gateway([{"id": index, "name": path} for index, path in enumerate(variants, 1)])

    result = execute_file_upload(7, (file,), port)

    assert result.failed == 1 and result.retry_files == (file,)
    assert all(path in result.files[0].message for path in variants)
    assert [call.method for call in session.calls] == ["GET"]


def test_safe_translation_import_submits_keys_translations_and_stage_in_one_json():
    file = artifact(2000)
    port, session = gateway(
        [{"id": 200, "name": file.path}],
        remote_rows(file, file_id=200, translation="旧译文", stage=0),
        {"file": {"id": 200}},
    )
    result = execute_file_upload(7, (file,), port, mode="trans_safe")
    assert result.succeeded == 1
    request = session.calls[2]
    assert request.path_url == "/api/projects/7/files/200/translation"
    payload = json.loads(request.body)
    assert payload["force"] is False and payload["skip"] is False
    assert payload["filename"] == "人名.json"
    assert len(payload["items"]) == 2000
    assert all(set(row) == {"key", "translation", "stage"} for row in payload["items"])
    assert len(session.calls) == 3


def test_force_import_sends_only_existing_keys_with_nonblank_different_translations():
    file = artifact(6)
    file = replace(
        file,
        entries=tuple(
            replace(entry, translation=translation)
            for entry, translation in zip(file.entries, ("译文", "", " \t", " 新译文 ", "缺失", "新填"), strict=True)
        ),
    )
    remote = remote_rows(file, legacy=True, stage=1)
    remote = [row for row in remote if row["key"] != "4"]
    for row, translation in zip(remote, ("译文", "保留一", "保留二", "旧译文", ""), strict=True):
        row["translation"] = translation
    port, session = gateway([{"id": 20, "name": file.path}], remote, {"file": {"id": 20}})
    receipt = execute_file_upload(7, (file,), port, mode="trans_force")

    assert receipt.succeeded == 1 and not receipt.retry_files
    assert [call.method for call in session.calls] == ["GET", "GET", "POST"]
    assert session.calls[1].path_url == session.calls[2].path_url == "/api/projects/7/files/20/translation"
    payload = json.loads(session.calls[2].body)
    assert payload["force"] is True
    assert payload["items"] == [
        {"key": "3", "translation": " 新译文 ", "stage": 3},
        {"key": "5", "translation": "新填", "stage": 3},
    ]


def test_force_import_does_not_read_or_write_a_file_with_only_blank_translations():
    file = artifact()
    file = replace(file, entries=(replace(file.entries[0], translation="　\t"),))
    port, session = gateway([{"id": 20, "name": file.path}])
    receipt = execute_file_upload(7, (file,), port, mode="trans_force")

    assert receipt.files[0].status == "unchanged" and not receipt.retry_files
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "remote",
    [
        requests.Timeout("read timed out"),
        (413, {}, {}),
        {"results": [{"key": "0", "translation": "旧"}]},
        [{"key": "0", "translation": "旧", "file": 21}],
        [{"key": "0"}],
        [{"key": "0", "translation": "旧"}, {"key": "0", "translation": "旧"}],
    ],
)
def test_force_import_never_writes_when_a_remote_comparison_is_unavailable(remote):
    file = artifact()
    port, session = gateway([{"id": 20, "name": file.path}], remote)
    receipt = execute_file_upload(7, (file,), port, mode="trans_force")

    assert receipt.files[0].status == "failed" and receipt.retry_files == (file,)
    assert [call.method for call in session.calls] == ["GET", "GET"]


def test_force_import_reloads_comparison_when_write_detects_a_replaced_file():
    file = artifact()
    port, session = gateway(
        [{"id": 20, "name": file.path}],
        remote_rows(file, legacy=True, translation="旧"),
        (404, {}, {}),
        [{"id": 21, "name": file.path}],
        remote_rows(file, file_id=21, legacy=True),
    )
    receipt = execute_file_upload(7, (file,), port, mode="trans_force")

    assert receipt.files[0].status == "unchanged" and not receipt.retry_files
    assert [(call.method, call.path_url) for call in session.calls] == [
        ("GET", "/api/projects/7/files"),
        ("GET", "/api/projects/7/files/20/translation"),
        ("POST", "/api/projects/7/files/20/translation"),
        ("GET", "/api/projects/7/files"),
        ("GET", "/api/projects/7/files/21/translation"),
    ]


@pytest.mark.parametrize("count", [ARTIFACT_FILE_THRESHOLD - 1, ARTIFACT_FILE_THRESHOLD])
def test_force_comparison_uses_fresh_selected_archive_at_download_threshold(monkeypatch, count):
    files = tuple(artifact(path=f"A.esp/{index}.json") for index in range(count))
    inventory = [{"id": index + 1, "name": file.path} for index, file in enumerate(files)]
    inventory.append({"id": 1000, "name": "B.esp/other.json"})
    package = []

    class Download:
        def __init__(self, config):
            pass

        def download(self, project_id, path, *, cancellation, progress):
            assert project_id == 7
            package.append(path)
            with ZipFile(path, "w") as zip_file:
                for index, file in enumerate(files):
                    row = wire_records(file)[0]
                    row.update(id=index + 100, fileId=index + 1)
                    if index == 0:
                        row["translation"] = "旧译文"
                    zip_file.writestr(f"raw/{file.path}.json", json.dumps([row]))
                zip_file.writestr("raw/B.esp/other.json.json", "unrelated data")

    def archive(config):
        return ArtifactFileReads(config, downloader_factory=Download)

    monkeypatch.setattr("transbridge.paratranz.file_publication.ArtifactFileReads", archive)
    use_archive = count == ARTIFACT_FILE_THRESHOLD
    children = []

    def session_factory():
        session = Responses(({"file": {"id": 1}},))
        children.append(session)
        return session

    if use_archive:
        port, session = gateway(inventory, session_factory=session_factory)
    else:
        port, session = gateway(inventory, *(remote_rows(file, file_id=index + 1) for index, file in enumerate(files)))
    receipt = execute_file_upload(7, files, port, mode="trans_force")

    assert receipt.succeeded == count and not receipt.retry_files
    if use_archive:
        assert len(package) == 1 and not package[0].parent.exists()
        assert [call.method for call in session.calls] == ["GET"]
        (write,) = [call for child in children for call in child.calls]
        assert write.method == "POST" and write.path_url == "/api/projects/7/files/1/translation"
        assert json.loads(write.body)["items"] == [
            {"key": wire_records(files[0])[0]["key"], "translation": "译文", "stage": 3}
        ]
    else:
        assert not package
        assert [call.path_url for call in session.calls[1:]] == [
            f"/api/projects/7/files/{file['id']}/translation" for file in inventory[:-1]
        ]
        assert all(call.method == "GET" for call in session.calls)


def test_force_archive_failure_cleans_up_and_keeps_files_for_manual_retry(monkeypatch):
    files = tuple(artifact(path=f"A.esp/{index}.json") for index in range(ARTIFACT_FILE_THRESHOLD))
    inventory = [{"id": index + 1, "name": file.path} for index, file in enumerate(files)]
    package = []

    class Download:
        def __init__(self, config):
            pass

        def download(self, project_id, path, **kwargs):
            package.append(path)
            path.write_bytes(b"invalid zip")

    monkeypatch.setattr(
        "transbridge.paratranz.file_publication.ArtifactFileReads",
        lambda config: ArtifactFileReads(config, downloader_factory=Download),
    )
    port, session = gateway(inventory)
    receipt = execute_file_upload(7, files, port, mode="trans_force")

    assert receipt.retry_files == files and all(item.status == "failed" for item in receipt.files)
    assert len(package) == 1 and not package[0].parent.exists()
    assert [call.method for call in session.calls] == ["GET"]


@pytest.mark.parametrize("status", ["hashMatched", "unchanged"])
def test_server_noop_is_success_without_followup_checks(status):
    port, session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}], remote_rows(artifact(), translation="旧"), {"status": status}
    )
    result = execute_file_upload(7, (artifact(),), port, mode="trans_safe")
    assert result.succeeded == 1 and result.failed == 0
    assert len(session.calls) == 3


def test_both_creates_with_enveloped_id_then_imports_stage_without_string_reads():
    port, session = gateway([], {"file": {"id": 20}}, {"file": {"id": 20}})
    result = execute_file_upload(7, (artifact(),), port, mode="both")
    assert result.succeeded == 1
    assert result.files[0].source_applied and result.files[0].translation_applied
    assert [call.path_url for call in session.calls] == [
        "/api/projects/7/files",
        "/api/projects/7/files",
        "/api/projects/7/files/20/translation",
    ]


@pytest.mark.parametrize("translation", ["", " \t"])
def test_both_new_file_imports_blank_translation_with_stage(translation):
    file = artifact()
    file = replace(file, entries=(replace(file.entries[0], translation=translation, stage=5),))
    port, session = gateway([], {"file": {"id": 20}}, {"file": {"id": 20}})

    receipt = execute_file_upload(7, (file,), port, mode="both")

    assert receipt.succeeded == 1 and not receipt.retry_files
    assert receipt.files[0].source_applied and receipt.files[0].translation_applied
    assert [call.method for call in session.calls] == ["GET", "POST", "POST"]
    assert json.loads(session.calls[2].body)["items"] == [
        {"key": "00000000|TB2|0", "translation": translation, "stage": 5}
    ]


@pytest.mark.parametrize("refreshed_path", ["A.esp/人名.json", "a.ESP/人名.json"])
def test_deleted_recreated_file_uses_current_path_id_after_one_404_refresh(refreshed_path):
    port, session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}],
        (404, {}, {}),
        [{"id": 30, "name": refreshed_path}],
        remote_rows(artifact(), file_id=30, original="Previous source"),
        {"file": {"id": 30}},
    )
    result = execute_file_upload(7, (artifact(),), port)
    assert result.succeeded == 1
    assert [call.path_url for call in session.calls] == [
        "/api/projects/7/files",
        "/api/projects/7/files/20/translation",
        "/api/projects/7/files",
        "/api/projects/7/files/30/translation",
        "/api/projects/7/files/30",
    ]


def test_timeout_is_uncertain_and_never_replayed():
    port, session = gateway([], requests.Timeout("response lost"))
    result = execute_file_upload(7, (artifact(),), port)
    assert result.files[0].status == "uncertain"
    assert len(session.calls) == 2


@pytest.mark.parametrize("status", [403, 423])
def test_permission_and_server_lock_are_file_failures_without_string_fallback(status):
    port, session = gateway([{"id": 20, "name": "A.esp/人名.json"}], (status, {}, {}))
    result = execute_file_upload(7, (artifact(),), port, mode="trans_safe")
    assert result.files[0].status == "failed"
    assert len(session.calls) == 2


def test_rate_limit_waits_retry_after_then_retries_only_rejected_request(monkeypatch):
    port, session = gateway((429, {}, {"Retry-After": "12"}), [])
    waits, clock = [], [0.0]

    def wait(delay):
        waits.append(delay)
        clock[0] += delay

    monkeypatch.setattr("transbridge.paratranz.file_publication.monotonic", lambda: clock[0])
    monkeypatch.setattr(port.cancellation, "wait", wait)
    assert port.inventory(7) == {}
    assert waits == [12]
    assert len(session.calls) == 2


def test_empty_import_is_not_reported_as_success():
    port, session = gateway([], {"status": "empty"})
    result = execute_file_upload(7, (artifact(),), port)
    assert result.failed == 1
    assert "empty" in result.files[0].message
    assert len(session.calls) == 2


def test_cancelled_task_makes_no_requests():
    cancel = Event()
    cancel.set()
    port, session = gateway(cancel=cancel)
    result = execute_file_upload(7, (artifact(),), port, cancelled=cancel.is_set)
    assert result.files[0].status == "cancelled" and session.calls == []


def test_413_splits_real_files_preserving_wire_order():
    port, session = gateway([], (413, {}, {}), {"file": {"id": 20}}, {"file": {"id": 21}})
    result = execute_file_upload(7, (artifact(4, "A.esp.json"),), port)
    assert [row.path for row in result.files] == ["A.esp_1.json", "A.esp_2.json"]
    assert result.succeeded == 2
    assert b'"key": "00000000|TB2|0"' in session.calls[2].body
    assert b'"key": "00000002|TB2|2"' in session.calls[3].body


def test_duplicate_file_keys_fail_locally_without_post():
    file = artifact()
    file = CategorizedFile(file.source_id, file.category_id, file.path, file.entries * 2)
    port, session = gateway([])
    result = execute_file_upload(7, (file,), port)
    assert result.failed == 1
    assert all(call.method == "GET" for call in session.calls)


def test_oversized_translation_import_chunks_inside_same_file():
    port, session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}],
        remote_rows(artifact(4), legacy=True, translation="旧"),
        (413, {}, {}),
        {"file": {"id": 20}},
        {"file": {"id": 20}},
    )
    result = execute_file_upload(7, (artifact(4),), port, mode="trans_force")
    assert result.succeeded == 1
    assert all(call.path_url == "/api/projects/7/files/20/translation" for call in session.calls[1:])
    assert [len(json.loads(call.body)["items"]) for call in session.calls[2:]] == [4, 2, 2]


def test_single_oversized_translation_does_not_create_split_files():
    port, session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}], remote_rows(artifact(), translation=""), (413, {}, {})
    )
    result = execute_file_upload(7, (artifact(),), port, mode="trans_force")
    assert result.failed == 1
    assert len(session.calls) == 3


def test_both_reports_source_committed_when_translation_permission_denied():
    port, _session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}],
        remote_rows(artifact(), original="Previous source", translation="旧"),
        {"file": {"id": 20}},
        (403, {}, {}),
    )
    result = execute_file_upload(7, (artifact(),), port, mode="both")
    assert result.failed == 1
    assert result.files[0].source_applied and not result.files[0].translation_applied
    assert "原文已提交" in result.files[0].message


def test_permission_failure_after_source_commit_stops_following_files():
    port, session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}],
        remote_rows(artifact(), original="Previous source", translation="旧"),
        {"file": {"id": 20}},
        (403, {}, {}),
    )
    result = execute_file_upload(7, (artifact(), artifact(path="B.esp/人名.json")), port, mode="both")
    assert [row.status for row in result.files] == ["failed", "skipped"]
    assert len(session.calls) == 4


def test_multiple_windows_share_serial_requests_and_can_cancel_while_queued(monkeypatch):
    budget = RequestBudget(0, max_concurrent=1)
    first, first_session = gateway([], budget=budget)
    cancel = Event()
    second, second_session = gateway([], budget=budget, cancel=cancel)
    entered, release, queued = Event(), Event(), Event()
    send = first_session.send

    def hold(request, **kwargs):
        entered.set()
        assert release.wait(3)
        return send(request, **kwargs)

    def cancel_wait(timeout):
        queued.set()
        cancel.set()
        return True

    monkeypatch.setattr(first_session, "send", hold)
    monkeypatch.setattr(second.cancellation, "wait", cancel_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(first.inventory, 7)
        assert entered.wait(3)
        waiting = pool.submit(second.inventory, 7)
        assert queued.wait(3)
        try:
            with pytest.raises(Exception, match="取消"):
                waiting.result(timeout=3)
            assert second_session.calls == []
        finally:
            release.set()
        assert active.result(timeout=3) == {}


def test_file_errors_do_not_log_server_echoed_translation_or_credentials(caplog):
    port, _session = gateway(
        [],
        (
            500,
            {
                "original": "private-source-text",
                "translation": "private-translation",
                "token": "file-publication-test-token",
            },
            {},
        ),
    )
    result = execute_file_upload(7, (artifact(),), port)
    assert result.files[0].status == "uncertain"
    assert "private-source-text" not in caplog.text
    assert "private-translation" not in caplog.text
    assert "file-publication-test-token" not in caplog.text
