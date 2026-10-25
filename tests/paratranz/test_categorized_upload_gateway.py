"""Capture prepared HTTP requests without sending any real ParaTranz traffic."""

from collections import deque
import json
import logging
from threading import Event
from urllib.parse import parse_qs, quote, urlsplit

import pytest
import requests

from transbridge.application.categorized_upload.models import FileCreationRejected
from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.paratranz.categorized_upload import CategorizedUploadGateway


class CaptureSession(requests.Session):
    def __init__(self, responses):
        super().__init__()
        self.responses = deque(responses)
        self.calls = []
        self.on_send = None

    def send(self, request, **kwargs):
        self.calls.append(request)
        if self.on_send is not None:
            self.on_send(request)
        value = self.responses.popleft()
        if isinstance(value, Exception):
            raise value
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        return response


def _gateway(responses, *, cancel=None, status=None):
    session = CaptureSession(responses)
    config = ParatranzConfig(
        token="test-categorized-token",
        base_url="https://paratranz.example/api",
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    return CategorizedUploadGateway(config, session=session, cancel=cancel, status=status), session


def _entry(remote_id=11, file_id=1, **overrides):
    return {
        "id": remote_id,
        "fileId": file_id,
        "key": "Name:00000123|0~NPC_:FULL",
        "original": "Original",
        "translation": "原译文",
        "context": "00000003|NPC_:FULL",
        "stage": 3,
        **overrides,
    }


def _local():
    return TranslationEntry("same", "same", "Original", "译文", 1, "00000003|NPC_:FULL")


def _body(call):
    return json.loads(call.body)


def test_path_lookup_reads_every_page_only_in_the_selected_file():
    files = [{"id": 1, "name": "A.json"}, {"id": 2, "name": "unrelated.json"}]
    first_page = [_entry(index + 1, 1) for index in range(800)]
    gateway, session = _gateway([files, {"results": first_page}, {"results": [_entry(801)]}])

    matches = gateway.find_files(7, "A.json")

    assert len(matches) == 1 and len(matches[0].entries) == 801
    assert {entry.file_id for entry in matches[0].entries} == {1}
    assert [parse_qs(urlsplit(call.url).query) for call in session.calls[1:]] == [
        {"file": ["1"], "page": ["1"], "pageSize": ["800"]},
        {"file": ["1"], "page": ["2"], "pageSize": ["800"]},
    ]
    assert all(call.method == "GET" for call in session.calls)


def test_file_path_guard_reads_only_metadata():
    gateway, session = _gateway([{"id": 4, "name": "人名.json", "folder": "A.esp"}])
    assert gateway.read_file_path(7, 4) == "A.esp/人名.json"
    assert [(call.method, urlsplit(call.url).path) for call in session.calls] == [("GET", "/api/projects/7/files/4")]


@pytest.mark.parametrize("payload", [None, [], {"id": 5, "name": "A.esp.json"}, {"id": 4}])
def test_file_path_guard_rejects_malformed_or_wrong_identity_metadata(payload):
    gateway, session = _gateway([payload])
    with pytest.raises(ValueError):
        gateway.read_file_path(7, 4)
    assert len(session.calls) == 1


def test_live_path_lookup_reads_matching_files_without_fetching_unrelated_entries():
    gateway, session = _gateway([
        [{"id": 1, "name": "unrelated.json"}, {"id": 4, "name": "人名.json", "folder": "A.esp"}],
        [_entry(11, 4)],
    ])

    matches = gateway.find_files(7, "A.esp/人名.json")

    assert len(matches) == 1 and matches[0].file_id == 4
    assert len(session.calls) == 2
    assert parse_qs(urlsplit(session.calls[-1].url).query)["file"] == ["4"]


@pytest.mark.parametrize("operation", ["entry", "path"])
@pytest.mark.parametrize("status", [404, 403, 500])
def test_scoped_reads_treat_only_explicit_404_as_absence(operation, status, monkeypatch):
    gateway, session = _gateway([{"message": "resource unavailable"}])
    send = session.send

    def respond(request, **kwargs):
        response = send(request, **kwargs)
        response.status_code = status
        return response

    monkeypatch.setattr(session, "send", respond)
    read = {"entry": gateway.read_entry, "path": gateway.read_file_path}[operation]
    if status == 404:
        assert read(7, 4) is None
    else:
        with pytest.raises(ExternalServiceError):
            read(7, 4)
    assert [call.method for call in session.calls] == ["GET"]


@pytest.mark.parametrize("operation", ["entry", "path"])
def test_scoped_read_timeout_is_not_absence(operation):
    gateway, session = _gateway([requests.Timeout("read failed")])
    read = {"entry": gateway.read_entry, "path": gateway.read_file_path}[operation]
    with pytest.raises(ExternalServiceError) as raised:
        read(7, 4)
    assert raised.value.category is ExternalServiceCategory.TIMEOUT
    assert len(session.calls) == 1


def test_path_lookup_respects_server_page_size_instead_of_silently_truncating():
    gateway, session = _gateway([
        [{"id": 1, "name": "A.esp/人名.json"}],
        {"page": 1, "pageSize": 1, "pageCount": 2, "rowCount": 2, "results": [_entry(11)]},
        {"page": 2, "pageSize": 1, "pageCount": 2, "rowCount": 2, "results": [_entry(12)]},
    ])

    snapshot = gateway.find_files(7, "A.esp/人名.json")

    assert [entry.remote_id for entry in snapshot[0].entries] == [11, 12]
    assert parse_qs(urlsplit(session.calls[-1].url).query)["page"] == ["2"]


def test_path_lookup_reports_page_progress_without_exposing_payloads():
    messages = []
    gateway, session = _gateway(
        [
            [{"id": 1, "name": "A.json"}, {"id": 2, "name": "unrelated.json"}],
            {"page": 1, "pageSize": 1, "pageCount": 2, "rowCount": 2, "results": [_entry(11)]},
            {"page": 2, "pageSize": 1, "pageCount": 2, "rowCount": 2, "results": [_entry(12)]},
        ],
        status=messages.append,
    )

    matches = gateway.find_files(7, "A.json")

    assert len(matches[0].entries) == 2
    assert any("第 1 页" in message for message in messages)
    assert any("第 2 页" in message and "已读取 1 条" in message for message in messages)
    assert [call.method for call in session.calls] == ["GET"] * 3
    assert all(
        private not in "\n".join(messages)
        for private in ("test-categorized-token", "Original", "原译文", "00000003|NPC_:FULL", _entry()["key"])
    )


def test_empty_path_lookup_does_not_request_entries():
    gateway, session = _gateway([[]])
    assert gateway.find_files(7, "A.json") == ()
    assert len(session.calls) == 1


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
def test_path_lookup_accepts_complete_file_list_envelopes_without_extra_requests(payload):
    files = payload.get("files", payload.get("results"))
    gateway, session = _gateway([payload, *([[_entry()]] if files else [])])

    snapshot = gateway.find_files(7, "A.json")

    assert len(snapshot) == len(files)
    assert len(session.calls) == 1 + len(files)
    assert all(call.method == "GET" for call in session.calls)
    if files:
        assert snapshot[0].entries[0].remote_id == 11


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
def test_path_lookup_rejects_invalid_file_list_pagination_metadata(metadata):
    gateway, session = _gateway([{"files": [], **metadata}])

    with pytest.raises(ValueError, match="云端"):
        gateway.find_files(7, "A.json")

    assert [call.method for call in session.calls] == ["GET"]


@pytest.mark.parametrize(
    "files, metadata",
    [
        ([], {"rowCount": 1, "pageCount": 2}),
        ([], {"rowCount": 1}),
        ([], {"rowCount": 0, "pageCount": 2}),
        ([{"id": 1, "name": "A.json"}], {"rowCount": 1, "pageCount": 2}),
        ([{"id": 1, "name": "A.json"}], {"pageCount": 0}),
        ([{"id": 1, "name": "A.json"}], {"pageSize": 1}),
        ([{"id": 1, "name": "A.json"}], {"rowCount": 2, "pageCount": 1}),
        ([{"id": 1, "name": "A.json"}], {"rowCount": 0}),
        ([{"id": 1, "name": "A.json"}, {"id": 2, "name": "B.json"}], {"rowCount": 2, "pageCount": 1, "pageSize": 1}),
    ],
)
def test_path_lookup_rejects_incomplete_or_contradictory_file_list_before_reading_entries(files, metadata):
    gateway, session = _gateway([{"files": files, **metadata}])

    with pytest.raises(ValueError, match="云端"):
        gateway.find_files(7, "A.json")

    assert [call.method for call in session.calls] == ["GET"]


@pytest.mark.parametrize("operation", ["file", "entry", "update"])
def test_mutation_feedback_distinguishes_upload_and_readback_without_extra_requests(operation):
    messages = []
    local = _local()
    responses = {
        "file": [{"file": {"id": 4, "name": "A.esp/人名.json"}}, [_entry(11, 4, key="same")]],
        "entry": [{"id": 11}, _entry(11, 4, key="same", translation=local.translation, stage=local.stage)],
        "update": [_entry(), {}, _entry(translation=local.translation, stage=local.stage)],
    }
    gateway, session = _gateway(responses[operation], status=messages.append)
    actions = {
        "file": lambda: gateway.create_file(7, "A.esp/人名.json", (local,)),
        "entry": lambda: gateway.create_entry(7, 4, local),
        "update": lambda: gateway.update_translation(7, 11, local.translation, local.stage),
    }

    actions[operation]()

    upload_index = next(index for index, message in enumerate(messages) if message.startswith("上传"))
    readback_index = next(index for index, message in enumerate(messages) if message.startswith("回读核实"))
    assert upload_index < readback_index
    if operation == "update":
        assert messages[0].startswith("写入前核查")
    if operation == "file":
        assert any("第 1 页" in message for message in messages[readback_index:])
    expected_methods = ["POST", "GET"] if operation in {"file", "entry"} else ["GET", "PUT", "GET"]
    assert [call.method for call in session.calls] == expected_methods
    assert all(
        private not in "\n".join(messages)
        for private in ("test-categorized-token", "Original", "译文", "00000003|NPC_:FULL", _entry()["key"])
    )


@pytest.mark.parametrize(
    "pages",
    [
        [
            {"page": 1, "pageSize": 1, "pageCount": 2, "rowCount": 2, "results": [_entry(11)]},
            {"page": 2, "pageSize": 1, "pageCount": 2, "rowCount": 2, "results": []},
        ],
        [
            {"page": 1, "pageSize": 1, "pageCount": 2, "rowCount": 2, "results": [_entry(11)]},
            {"page": 2, "pageSize": 1, "pageCount": 3, "rowCount": 3, "results": [_entry(12)]},
        ],
        [{"page": 1, "pageSize": 800, "pageCount": 1, "rowCount": 2, "results": [_entry(11)]}],
    ],
)
def test_path_lookup_rejects_truncated_or_changing_page_totals(pages):
    gateway, _session = _gateway([[{"id": 1, "name": "A.esp/人名.json"}], *pages])

    with pytest.raises(ValueError):
        gateway.find_files(7, "A.esp/人名.json")


@pytest.mark.parametrize(
    "pages",
    [
        [{"results": [], "page": 1, "pageSize": 800, "pageCount": 2, "rowCount": 0}],
        [{"results": [_entry()], "page": 1, "pageSize": 800, "pageCount": 2, "rowCount": 1}],
        [{"results": [_entry()], "page": 1, "pageSize": 1, "pageCount": 0, "rowCount": 1}],
        [{"results": [_entry(11), _entry(12)], "page": 1, "pageSize": 1, "pageCount": 1, "rowCount": 2}],
        [{"results": [_entry()], "page": 1, "pageSize": 1, "pageCount": 2, "rowCount": 4}],
        [
            {"results": [_entry(11)], "page": 1, "pageSize": 1, "pageCount": 2, "rowCount": 2},
            [],
        ],
        [
            {"results": [_entry(11)], "page": 1, "pageSize": 1, "pageCount": 2},
            {"results": [], "page": 2, "pageSize": 1},
        ],
        [
            {"results": [_entry(11)], "page": 1, "pageSize": 1, "rowCount": 3},
            {"results": [_entry(12), _entry(13)], "page": 2, "pageSize": 2, "pageCount": 1, "rowCount": 3},
        ],
    ],
)
def test_path_lookup_rejects_contradictory_entry_page_counts_and_incomplete_metadata_free_tail(pages):
    gateway, session = _gateway([[{"id": 1, "name": "A.json"}], *pages])

    with pytest.raises(ValueError, match="云端"):
        gateway.find_files(7, "A.json")

    assert len(session.calls) == 1 + len(pages)
    assert all(call.method == "GET" for call in session.calls)


@pytest.mark.parametrize("field", ["page", "pageSize", "pageCount", "rowCount"])
def test_path_lookup_rejects_explicit_null_entry_page_metadata(field):
    gateway, session = _gateway([[{"id": 1, "name": "A.json"}], {"results": [], field: None}])

    with pytest.raises(ValueError, match="云端"):
        gateway.find_files(7, "A.json")

    assert len(session.calls) == 2


@pytest.mark.parametrize("page_count", [0, 1])
def test_path_lookup_accepts_consistent_empty_entry_pagination(page_count):
    gateway, session = _gateway([
        [{"id": 1, "name": "A.json"}],
        {"results": [], "page": 1, "pageSize": 800, "pageCount": page_count, "rowCount": 0},
    ])

    snapshot = gateway.find_files(7, "A.json")

    assert len(snapshot) == 1 and snapshot[0].entries == ()
    assert len(session.calls) == 2


def test_path_lookup_preserves_known_totals_when_complete_final_page_omits_metadata():
    gateway, session = _gateway([
        [{"id": 1, "name": "A.json"}],
        {"results": [_entry(11)], "page": 1, "pageSize": 1, "pageCount": 2, "rowCount": 2},
        [_entry(12)],
    ])

    snapshot = gateway.find_files(7, "A.json")

    assert [entry.remote_id for entry in snapshot[0].entries] == [11, 12]
    assert len(session.calls) == 3


@pytest.mark.parametrize(
    "responses",
    [
        [{"unexpected": []}],
        [[{"id": True, "name": "人名.json"}]],
        [[{"id": 1, "name": "人名.json"}, {"id": 1, "name": "其他.json"}], []],
        [[{"id": 1, "name": "人名.json"}], [_entry(), _entry()]],
        [[{"id": 1, "name": "人名.json"}], [_entry(file_id=2)]],
        [[{"id": 1, "name": "人名.json"}], [_entry(stage=True)]],
        [[{"id": 1, "name": "人名.json"}], [_entry(context=None)]],
        [[{"id": 1, "name": "人名.json"}], [_entry(key="")]],
    ],
)
def test_path_lookup_rejects_malformed_and_ambiguous_responses(responses):
    gateway, _session = _gateway(responses)

    with pytest.raises(ValueError):
        gateway.find_files(7, "人名.json")


@pytest.mark.parametrize("nested", [True, False])
def test_create_file_sends_real_json_and_chinese_folder_then_reads_imported_identities(nested):
    file = {"id": 4, "name": "人名.json", "folder": "主体.esp/"}
    gateway, session = _gateway([{"file": file} if nested else file, [_entry(11, 4, key="same")]])

    created = gateway.create_file(7, "主体.esp/人名.json", (_local(),))

    assert (created.file_id, created.path, created.entries[0].remote_id) == (4, "主体.esp/人名.json", 11)
    upload, verify = session.calls
    assert upload.method == "POST"
    assert upload.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    assert f"filename*=UTF-8''{quote('人名.json', safe='')}".encode() in upload.body
    assert b"\r\n\r\n[]\r\n" not in upload.body
    assert '"translation": "译文"'.encode() in upload.body
    assert 'name="path"\r\n\r\n主体.esp/\r\n'.encode() in upload.body
    assert verify.method == "GET"
    assert parse_qs(urlsplit(verify.url).query)["file"] == ["4"]


@pytest.mark.parametrize("response", [{}, {"file": {"id": True}}, {"file": {"id": 4, "name": "Other.json"}}])
def test_create_file_rejects_missing_identity_or_different_path(response):
    gateway, session = _gateway([response])

    with pytest.raises(ValueError):
        gateway.create_file(7, "A.esp/人名.json", (_local(),))
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", ["empty", "unknown"])
def test_file_import_rejects_unaccepted_content_without_retry_or_readback(status):
    gateway, session = _gateway([{"status": status}])
    expected = FileCreationRejected if status == "empty" else ValueError
    with pytest.raises(expected):
        gateway.create_file(7, "A.esp/人名.json", (_local(),))
    assert [call.method for call in session.calls] == ["POST"]


def test_response_diagnostics_preserve_error_fields_but_exclude_credentials_and_payloads(caplog):
    caplog.set_level(logging.INFO)
    gateway, session = _gateway([
        {
            "message": "not created test-categorized-token\nrequest finished",
            "error": {"code": "EMPTY_FILE", "translation": "hidden nested translation"},
            "token": "test-categorized-token",
            "original": "hidden original",
            "translation": "hidden translation",
            "context": "hidden context",
        }
    ])

    with pytest.raises(ValueError):
        gateway.create_file(7, "A.esp/人名.json", (_local(),))

    assert len(session.calls) == 1
    assert "EMPTY_FILE" in caplog.text and "not created" in caplog.text
    assert "test-categorized-token" not in caplog.text
    assert all(
        text not in caplog.text
        for text in ("hidden original", "hidden translation", "hidden context", "hidden nested translation")
    )


def test_http_error_diagnostics_include_status_and_reason_without_echoed_translation_data(caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    gateway, session = _gateway([
        {
            "code": "INVALID_FILE",
            "message": "file rejected",
            "original": "private original",
            "translation": "private translation",
        },
    ])
    send = session.send

    def reject(request, **kwargs):
        response = send(request, **kwargs)
        if request.method == "POST":
            response.status_code = 400
            response.headers["X-Request-ID"] = "request-failed-1"
        return response

    monkeypatch.setattr(session, "send", reject)
    with pytest.raises(ExternalServiceError):
        gateway.create_file(7, "A.esp/人名.json", (_local(),))

    records = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "transbridge.application.categorized_upload.diagnostics"
    ]
    failure = next(item for item in records if item["step"] == "operation_failed")
    assert failure["http_status"] == 400
    assert failure["request_id"] == "request-failed-1"
    assert failure["response"]["code"] == "INVALID_FILE"
    assert failure["response"]["message"] == "file rejected"
    assert "private original" not in caplog.text and "private translation" not in caplog.text
    assert [call.method for call in session.calls] == ["POST"]


def test_create_entry_preserves_wire_key_and_ordered_context_and_verifies_file():
    local = _local()
    expected = _entry(11, 4, key=local.key, translation=local.translation, stage=local.stage)
    gateway, session = _gateway([{"id": 11}, expected])

    created = gateway.create_entry(7, 4, local)

    assert (created.remote_id, created.file_id, created.key) == (11, 4, "same")
    assert _body(session.calls[0]) == {
        "fileId": 4,
        "key": "same",
        "original": "Original",
        "translation": "译文",
        "context": "00000003|NPC_:FULL",
        "stage": 1,
    }
    assert [(call.method, urlsplit(call.url).path) for call in session.calls] == [
        ("POST", "/api/projects/7/strings"),
        ("GET", "/api/projects/7/strings/11"),
    ]


def test_update_changes_only_translation_and_stage_with_before_after_identity_checks():
    gateway, session = _gateway([_entry(), {}, _entry(translation="更新译文", stage=5)])

    updated = gateway.update_translation(7, 11, "更新译文", 5)

    assert [call.method for call in session.calls] == ["GET", "PUT", "GET"]
    assert _body(session.calls[1]) == {"translation": "更新译文", "stage": 5}
    assert updated.translation == "更新译文"
    assert updated.original == "Original"


def test_created_entry_changed_before_update_is_rejected_before_put():
    local = _local()
    gateway, session = _gateway([
        {"id": 11},
        _entry(11, 1, key=local.key, translation=local.translation, stage=local.stage),
        _entry(11, 1, key=local.key, translation="Other translator changed this", stage=5),
    ])
    gateway.create_entry(7, 1, local)
    with pytest.raises(ExternalServiceError) as raised:
        gateway.update_translation(7, 11, "New local translation", 3)
    assert raised.value.category is ExternalServiceCategory.CONFLICT
    assert [call.method for call in session.calls] == ["POST", "GET", "GET"]


@pytest.mark.parametrize("operation", ["create", "update"])
def test_mutations_reject_server_response_that_changed_preserved_fields(operation):
    if operation == "create":
        gateway, _session = _gateway([{"id": 11}, _entry(file_id=99)])
    else:
        gateway, _session = _gateway([_entry(), {}, _entry(original="Changed", translation="Translation", stage=1)])
    with pytest.raises(ValueError):
        if operation == "create":
            gateway.create_entry(7, 4, _local())
        else:
            gateway.update_translation(7, 11, "Translation", 1)


@pytest.mark.parametrize("operation", ["file", "entry", "update"])
def test_timed_out_mutations_are_never_retransmitted(operation):
    responses = [_entry()] if operation == "update" else []
    gateway, session = _gateway([*responses, requests.Timeout("response lost")])
    actions = {
        "file": lambda: gateway.create_file(7, "A.esp/人名.json", (_local(),)),
        "entry": lambda: gateway.create_entry(7, 1, _local()),
        "update": lambda: gateway.update_translation(7, 11, "译文", 1),
    }

    with pytest.raises(ExternalServiceError) as raised:
        actions[operation]()

    assert raised.value.category is ExternalServiceCategory.TIMEOUT
    assert sum(call.method in {"POST", "PUT"} for call in session.calls) == 1


def test_cancellation_prevents_a_request_and_stops_after_a_possibly_completed_post():
    cancel = Event()
    gateway, session = _gateway([{"id": 11}], cancel=cancel)
    cancel.set()
    with pytest.raises(ExternalServiceError) as raised:
        gateway.create_entry(7, 1, _local())
    assert raised.value.category is ExternalServiceCategory.CANCELLED
    assert not session.calls

    cancel.clear()
    session.on_send = lambda _request: cancel.set()
    with pytest.raises(ExternalServiceError) as raised:
        gateway.create_entry(7, 1, _local())
    assert raised.value.category is ExternalServiceCategory.CANCELLED
    assert [call.method for call in session.calls] == ["POST"]


@pytest.mark.parametrize("path", ["../人名.json", "A//人名.json", "/人名.json", "A\\人名.json"])
def test_invalid_file_path_is_rejected_before_network_activity(path):
    gateway, session = _gateway([])
    with pytest.raises(ValueError):
        gateway.create_file(7, path, (_local(),))
    assert not session.calls


@pytest.mark.parametrize("entries", [(), (_local(), _local())])
def test_empty_or_duplicate_key_file_is_rejected_before_network_activity(entries):
    gateway, session = _gateway([])
    with pytest.raises(ValueError):
        gateway.create_file(7, "A.esp/人名.json", entries)
    assert session.calls == []


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "original", "context", "key", "fileId", "id"])
def test_file_import_rejects_missing_extra_or_changed_source_records(change):
    row = _entry(11, 4, key="same")
    rows = [row]
    if change == "missing":
        rows = []
    elif change == "extra":
        rows.append(_entry(12, 4, key="other"))
    elif change == "duplicate":
        rows.append(dict(row))
    else:
        row[change] = 99 if change == "fileId" else True if change == "id" else "changed"
    gateway, session = _gateway([{"file": {"id": 4, "name": "A.esp/人名.json"}}, rows])
    with pytest.raises(ValueError):
        gateway.create_file(7, "A.esp/人名.json", (_local(),))
    assert [call.method for call in session.calls] == ["POST", "GET"]
