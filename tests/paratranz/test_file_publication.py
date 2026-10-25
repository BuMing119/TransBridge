"""File publication contracts, using prepared HTTP requests without live services."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event

import pytest
import requests

from transbridge.application.categorized_upload.models import CategorizedFile
from transbridge.application.file_upload import execute_file_upload
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.converter.translation_entry import TranslationEntry
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


def gateway(*responses, cancel=None, budget=None):
    session = Responses(responses)
    config = ParatranzConfig(
        token="file-publication-test-token",
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    return FilePublicationGateway(config, session=session, cancel=cancel, budget=budget or RequestBudget(0)), session


def artifact(count=1, path="A.esp/人名.json"):
    return CategorizedFile(
        "a",
        "names",
        path,
        tuple(TranslationEntry(str(index), str(index), "Original", "译文", 3, "NPC_:FULL") for index in range(count)),
    )


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


def test_source_update_uses_incremental_and_server_hash_without_readback():
    port, session = gateway([{"id": 200, "name": "人名.json", "folder": "A.esp"}], {"status": "hashMatched"})
    result = execute_file_upload(7, (artifact(),), port)
    assert result.files[0].status == "unchanged"
    request = session.calls[1]
    assert request.path_url == "/api/projects/7/files/200"
    assert b'name="incremental"\r\n\r\ntrue' in request.body
    assert b'name="skip"\r\n\r\nfalse' in request.body
    assert len(session.calls) == 2


@pytest.mark.parametrize("mode,force", [("trans_safe", False), ("trans_force", True)])
def test_translation_import_submits_keys_translations_and_stage_in_one_json(mode, force):
    port, session = gateway([{"id": 200, "name": "A.esp/人名.json"}], {"file": {"id": 200}})
    result = execute_file_upload(7, (artifact(2000),), port, mode=mode)
    assert result.succeeded == 1
    request = session.calls[1]
    assert request.path_url == "/api/projects/7/files/200/translation"
    payload = json.loads(request.body)
    assert payload["force"] is force and payload["skip"] is False
    assert payload["filename"] == "人名.json"
    assert len(payload["items"]) == 2000
    assert all(set(row) == {"key", "translation", "stage"} for row in payload["items"])
    assert len(session.calls) == 2


@pytest.mark.parametrize("status", ["hashMatched", "unchanged"])
def test_server_noop_is_success_without_followup_checks(status):
    port, session = gateway([{"id": 20, "name": "A.esp/人名.json"}], {"status": status})
    result = execute_file_upload(7, (artifact(),), port, mode="trans_safe")
    assert result.succeeded == 1 and result.failed == 0
    assert len(session.calls) == 2


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


def test_deleted_recreated_file_uses_current_path_id_after_one_404_refresh():
    port, session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}],
        (404, {}, {}),
        [{"id": 30, "name": "A.esp/人名.json"}],
        {"file": {"id": 30}},
    )
    result = execute_file_upload(7, (artifact(),), port)
    assert result.succeeded == 1
    assert [call.path_url for call in session.calls] == [
        "/api/projects/7/files",
        "/api/projects/7/files/20",
        "/api/projects/7/files",
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
    waits = []
    monkeypatch.setattr(port.cancellation, "wait", lambda delay: waits.append(delay))
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
    assert b'"key": "0"' in session.calls[2].body and b'"key": "2"' in session.calls[3].body


def test_duplicate_file_keys_fail_locally_without_post():
    file = artifact()
    file = CategorizedFile(file.source_id, file.category_id, file.path, file.entries * 2)
    port, session = gateway([])
    result = execute_file_upload(7, (file,), port)
    assert result.failed == 1
    assert all(call.method == "GET" for call in session.calls)


def test_oversized_translation_import_chunks_inside_same_file():
    port, session = gateway(
        [{"id": 20, "name": "A.esp/人名.json"}], (413, {}, {}), {"file": {"id": 20}}, {"file": {"id": 20}}
    )
    result = execute_file_upload(7, (artifact(4),), port, mode="trans_force")
    assert result.succeeded == 1
    assert all(call.path_url == "/api/projects/7/files/20/translation" for call in session.calls[1:])
    assert [len(json.loads(call.body)["items"]) for call in session.calls[1:]] == [4, 2, 2]


def test_single_oversized_translation_does_not_create_split_files():
    port, session = gateway([{"id": 20, "name": "A.esp/人名.json"}], (413, {}, {}))
    result = execute_file_upload(7, (artifact(),), port, mode="trans_force")
    assert result.failed == 1
    assert len(session.calls) == 2


def test_both_reports_source_committed_when_translation_permission_denied():
    port, _session = gateway([{"id": 20, "name": "A.esp/人名.json"}], {"file": {"id": 20}}, (403, {}, {}))
    result = execute_file_upload(7, (artifact(),), port, mode="both")
    assert result.failed == 1
    assert result.files[0].source_applied and not result.files[0].translation_applied
    assert "原文已提交" in result.files[0].message


def test_permission_failure_after_source_commit_stops_following_files():
    port, session = gateway([{"id": 20, "name": "A.esp/人名.json"}], {"file": {"id": 20}}, (403, {}, {}))
    result = execute_file_upload(7, (artifact(), artifact(path="B.esp/人名.json")), port, mode="both")
    assert [row.status for row in result.files] == ["failed", "skipped"]
    assert len(session.calls) == 3


def test_multiple_windows_share_serial_requests_and_can_cancel_while_queued(monkeypatch):
    budget = RequestBudget(0)
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
