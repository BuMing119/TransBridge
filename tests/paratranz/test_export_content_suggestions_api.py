"""Offline wire contracts from ParaTranz OpenAPI 0.5.7, checked 2026-11-03."""

from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter

from transbridge.application.ports.paratranz import ExternalServiceError
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.paratranz.api import (
    ParatranzExportAPI,
    ParatranzFilesAPI,
    ParatranzStringsAPI,
    ParatranzSuggestionsAPI,
)
from transbridge.paratranz.paratranz_client import RetryPolicy


class _ResponseStream(BytesIO):
    def release_conn(self):
        self.close()


class _Wire(BaseAdapter):
    def __init__(self, payload=None, *, body=None, status=200):
        self.payload = payload
        self.body = body
        self.status = status
        self.requests = []
        self.responses = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        response = requests.Response()
        response.status_code = self.status
        response.request = request
        response.url = request.url
        response._content = self.body if self.body is not None else json.dumps(self.payload).encode()
        response.raw = _ResponseStream(response._content)
        response.headers["X-Es-Timedout"] = "true"
        self.responses.append(response)
        return response

    def close(self):
        pass


def _api(cls, payload=None, **kwargs):
    wire = _Wire(payload, **kwargs)
    api = cls(token="synthetic-api-contract", retry_policy=RetryPolicy(initial_backoff=0, maximum_backoff=0))
    api._session.mount("https://", wire)
    return api, wire


def _query(wire):
    return parse_qs(urlsplit(wire.requests[-1].url).query, keep_blank_values=True)


@pytest.mark.parametrize("payload", [None, {"id": 5, "createdAt": "2026-11-03T00:00:00Z", "total": 4}])
def test_latest_artifact_preserves_null_and_single_object(payload):
    api, wire = _api(ParatranzExportAPI, payload)
    assert api.get_latest_artifact(31) == payload
    assert wire.requests[0].method == "GET"
    assert urlsplit(wire.requests[0].url).path == "/api/projects/31/artifacts/latest"
    assert not _query(wire)


def test_artifact_history_preserves_pagination_wrapper():
    payload = {"page": 2, "pageSize": 3, "rowCount": 4, "pageCount": 2, "results": [{"id": 1}]}
    api, wire = _api(ParatranzExportAPI, payload)
    assert api.get_artifacts(31, page=2, page_size=3) == payload
    assert urlsplit(wire.requests[0].url).path == "/api/projects/31/artifacts"
    assert _query(wire) == {"page": ["2"], "pageSize": ["3"]}


def test_file_content_json_includes_revision_and_highlight_without_download_flag():
    payload = {"id": 9, "content": "原文", "highlight": {"key": "HELLO", "original": "原文"}}
    api, wire = _api(ParatranzFilesAPI, payload)
    assert api.get_file_content(31, 7, rev=9, tid=0) == payload
    assert urlsplit(wire.requests[0].url).path == "/api/projects/31/files/7/content"
    assert _query(wire) == {"rev": ["9"], "tid": ["0"]}


def test_file_content_attachment_preserves_bom_and_closes_response():
    body = b"\xef\xbb\xbf" + "原文\r\n".encode()
    api, wire = _api(ParatranzFilesAPI, body=body)
    assert api.get_file_content(31, 7, dl=True) == body
    assert _query(wire) == {"dl": ["1"]}
    assert wire.responses[0].raw.closed


def test_suggestions_zero_rate_ignore_and_raw_diagnostics():
    payload = [{"matching": 0, "contains": 1, "translation": "译文"}]
    api, wire = _api(ParatranzSuggestionsAPI, payload)
    assert api.get_suggestions(31, 8, scope="all", rate=0, ignore="12,34") == payload
    assert urlsplit(wire.requests[0].url).path == "/api/projects/31/strings/8/suggestions"
    assert _query(wire) == {"scope": ["all"], "rate": ["0"], "ignore": ["12,34"], "force": ["false"]}
    response = api.get_suggestions(31, 8, raw_response=True, force=True)
    try:
        assert response.headers["X-Es-Timedout"] == "true"
        assert response.json() == payload
        assert _query(wire) == {"scope": ["self"], "rate": ["70"], "force": ["true"]}
    finally:
        response.close()


def test_high_cost_suggestions_do_not_automatically_retry_rate_limit():
    api, wire = _api(ParatranzSuggestionsAPI, {"message": "slow down"}, status=429)
    with pytest.raises(ExternalServiceError) as error:
        api.get_suggestions(31, 8)
    assert error.value.status == 429
    assert len(wire.requests) == 1


@pytest.mark.parametrize(
    ("cls", "method", "args", "kwargs"),
    [
        (ParatranzExportAPI, "get_latest_artifact", (31,), {}),
        (ParatranzExportAPI, "get_artifacts", (31,), {}),
        (ParatranzFilesAPI, "get_file_content", (31, 7), {}),
        (ParatranzFilesAPI, "get_file_content", (31, 7), {"dl": True}),
        (ParatranzSuggestionsAPI, "get_suggestions", (31, 8), {}),
    ],
)
def test_cancelled_reads_never_send_a_request(cls, method, args, kwargs):
    api, wire = _api(cls)
    cancellation = CancellationToken()
    cancellation._cancel("contract cancellation")
    with pytest.raises(TaskCancelled):
        getattr(api, method)(*args, **kwargs, cancellation=cancellation)
    assert not wire.requests


def test_artifact_download_preserves_zip_bytes_and_closes_stream(tmp_path):
    payload = b"PK\x03\x04\x00\xff"
    api, wire = _api(ParatranzExportAPI, body=payload)
    destination = str(tmp_path / "artifact.zip")
    assert api.download_artifacts(31, destination) == destination
    assert (tmp_path / "artifact.zip").read_bytes() == payload
    assert wire.responses[0].raw.closed


def test_strings_task_and_zero_stage_filters_are_preserved():
    api, wire = _api(ParatranzStringsAPI, {"results": []})
    assert api.list_strings(31, task=9, stage=0) == {"results": []}
    assert _query(wire) == {"page": ["1"], "pageSize": ["50"], "task": ["9"], "stage": ["0"]}


@pytest.mark.parametrize("op", ["edit", "update"])
def test_batch_edit_translates_legacy_arguments_to_official_items(op):
    api, wire = _api(ParatranzStringsAPI, [8, 9])
    assert api.batch_strings(31, op, [8, 9], stage=0, translation="") == [8, 9]
    assert wire.requests[0].method == "PUT"
    assert json.loads(wire.requests[0].body) == {
        "op": "edit",
        "items": [{"id": 8, "stage": 0, "translation": ""}, {"id": 9, "stage": 0, "translation": ""}],
    }


def test_batch_edit_per_item_fields_and_delete_empty_response():
    items = [{"id": 8, "key": "key", "original": "原文", "context": "上下文"}]
    api, wire = _api(ParatranzStringsAPI, [8])
    assert api.batch_strings(31, "edit", items=items) == [8]
    assert json.loads(wire.requests[0].body) == {"op": "edit", "items": items}
    api, wire = _api(ParatranzStringsAPI, body=b"")
    assert api.batch_strings(31, "delete", [8]) is None
    assert json.loads(wire.requests[0].body) == {"op": "delete", "id": [8]}


@pytest.mark.parametrize("kwargs", [{"items": [{}]}, {"ids": [1], "items": [{"id": 1}]}])
def test_batch_edit_rejects_ambiguous_or_incomplete_input_before_network(kwargs):
    api, wire = _api(ParatranzStringsAPI)
    with pytest.raises(ValueError):
        api.batch_strings(31, "edit", **kwargs)
    assert not wire.requests


def test_batch_write_does_not_retry_unknown_outcome():
    api, wire = _api(ParatranzStringsAPI, {}, status=503)
    with pytest.raises(ExternalServiceError):
        api.batch_strings(31, "update", [8], stage=1)
    assert len(wire.requests) == 1


@pytest.mark.parametrize("name", ["ParatranzAnnouncementsAPI", "ParatranzTasksAPI", "ParatranzSuggestionsAPI"])
def test_new_api_classes_are_available_from_both_public_packages(name):
    import transbridge.paratranz as public
    import transbridge.paratranz.api as endpoints

    assert getattr(public, name) is getattr(endpoints, name)
    assert name in public.__all__ and name in endpoints.__all__
