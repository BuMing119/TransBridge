"""Task endpoint contracts from OpenAPI 0.5.7, without a live ParaTranz project."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.api.paratranz_tasks_api import ParatranzTasksAPI
from transbridge.paratranz.paratranz_client import RetryPolicy


class _TaskSession(requests.Session):
    """Use requests' real encoding while replacing only the HTTP send boundary."""

    def __init__(self, payload, *, status=200):
        super().__init__()
        self.payload = payload
        self.status = status
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)
        response = requests.Response()
        response.status_code = self.status
        response.url = request.url
        response.request = request
        response._content = b"" if self.payload is None else json.dumps(self.payload).encode()
        response.headers["Content-Type"] = "application/json"
        response.headers["X-Request-ID"] = "contract-001"
        return response


def _api(payload, *, status=200):
    session = _TaskSession(payload, status=status)
    api = ParatranzTasksAPI(
        config=ParatranzConfig(
            token="synthetic-task-token",
            base_url="https://paratranz.invalid/api",
            credential_store=UnavailableCredentialStore(),
            environment={},
        ),
        session=session,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff=0, maximum_backoff=0),
    )
    return api, session


@pytest.mark.parametrize(
    ("method", "args", "verb", "suffix", "body", "payload"),
    [
        (
            "create_task",
            (41, {"title": "翻译", "goal": {"translate": 0.8}}),
            "POST",
            "",
            {"title": "翻译", "goal": {"translate": 0.8}},
            {"id": 7},
        ),
        ("get_task", (41, 7), "GET", "/7", None, {"id": 7, "words": {"total": 12}, "extraField": True}),
        ("operate_task", (41, 7, "pin"), "POST", "/7", {"op": "pin"}, {"id": 7}),
        ("operate_task", (41, 7, "unpin"), "POST", "/7", {"op": "unpin"}, {"id": 7}),
        ("operate_task", (41, 7, "finish"), "POST", "/7", {"op": "finish"}, {"id": 7, "status": 1}),
        (
            "update_task",
            (41, 7, {"deadline": None, "bumped": False, "content": "", "extra": {"members": []}}),
            "PUT",
            "/7",
            {"deadline": None, "bumped": False, "content": "", "extra": {"members": []}},
            {"id": 7},
        ),
        ("delete_task", (41, 7), "DELETE", "/7", None, None),
        ("list_task_files", (41, 7), "GET", "/7/files", None, [{"file": {"id": 9}, "mode": 0}]),
        ("add_task_file", (41, 7, 9), "POST", "/7/files", {"fileId": 9, "mode": 0}, {"id": 3}),
        ("add_task_file", (41, 7, 9, 1), "POST", "/7/files", {"fileId": 9, "mode": 1}, {"id": 3}),
        (
            "update_task_strings",
            (41, 7, "add", [10, 11]),
            "PUT",
            "/7/strings",
            {"op": "add", "items": [10, 11]},
            {"added": 1, "skipped": 1},
        ),
        (
            "update_task_strings",
            (41, 7, "remove", [10, 11]),
            "PUT",
            "/7/strings",
            {"op": "remove", "items": [10, 11]},
            {"removed": 1, "skipped": 1},
        ),
    ],
)
def test_task_request_and_response_contract(method, args, verb, suffix, body, payload):
    api, session = _api(payload)

    result = getattr(api, method)(*args)

    request = session.sent[0]
    assert request.method == verb
    assert request.url == f"https://paratranz.invalid/api/projects/41/tasks{suffix}"
    assert (json.loads(request.body) if request.body else None) == body
    assert result == payload


@pytest.mark.parametrize(
    ("kwargs", "query"),
    [({}, {"page": ["1"], "pageSize": ["20"]}), ({"page": 2, "page_size": 3}, {"page": ["2"], "pageSize": ["3"]})],
)
def test_task_list_preserves_official_pagination_wrapper(kwargs, query):
    payload = {"page": 2, "pageSize": 3, "rowCount": 5, "pageCount": 2, "results": [{"id": 7}]}
    api, session = _api(payload)

    assert api.list_tasks(41, **kwargs) == payload
    request = session.sent[0]
    assert request.method == "GET"
    assert urlsplit(request.url).path == "/api/projects/41/tasks"
    assert parse_qs(urlsplit(request.url).query) == query


def test_remove_task_files_uses_repeated_query_values_and_no_json_body():
    api, session = _api(None)

    assert api.remove_task_files(41, 7, [8, 9]) is None
    request = session.sent[0]
    assert request.method == "DELETE"
    assert request.url == "https://paratranz.invalid/api/projects/41/tasks/7/files?file=8&file=9"
    assert request.body is None


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("list_tasks", (41,)),
        ("create_task", (41, {"title": "任务", "goal": {"translate": 1}})),
        ("get_task", (41, 7)),
        ("operate_task", (41, 7, "finish")),
        ("update_task", (41, 7, {"bumped": False})),
        ("delete_task", (41, 7)),
        ("list_task_files", (41, 7)),
        ("add_task_file", (41, 7, 9)),
        ("remove_task_files", (41, 7, [9])),
        ("update_task_strings", (41, 7, "add", [10])),
    ],
)
def test_every_task_endpoint_checks_cancellation_before_network(method, args):
    api, session = _api({})
    cancellation = CancellationToken()
    cancellation._cancel("cancel task contract")

    with pytest.raises(TaskCancelled):
        getattr(api, method)(*args, cancellation=cancellation)

    assert session.sent == []


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("create_task", (41, {"title": "任务", "goal": {"translate": 1}})),
        ("operate_task", (41, 7, "pin")),
        ("update_task", (41, 7, {"bumped": True})),
        ("delete_task", (41, 7)),
        ("add_task_file", (41, 7, 9)),
        ("remove_task_files", (41, 7, [9])),
        ("update_task_strings", (41, 7, "add", [10])),
    ],
)
def test_writes_do_not_repeat_after_unknown_server_failure(method, args):
    api, session = _api({"message": "unknown write outcome"}, status=503)

    with pytest.raises(ExternalServiceError) as captured:
        getattr(api, method)(*args)

    assert captured.value.status == 503
    assert captured.value.request_id == "contract-001"
    assert len(session.sent) == 1


@pytest.mark.parametrize("status", [400, 403, 404])
def test_task_string_errors_propagate_without_turning_into_empty_success(status):
    api, session = _api({"message": "task is finished, forbidden or missing"}, status=status)

    with pytest.raises(ExternalServiceError) as captured:
        api.update_task_strings(41, 7, "add", [10])

    assert captured.value.status == status
    assert len(session.sent) == 1


def test_task_list_rejects_an_array_instead_of_official_page_object():
    api, _ = _api([])

    with pytest.raises(ExternalServiceError) as captured:
        api.list_tasks(41)

    assert captured.value.category is ExternalServiceCategory.INVALID_RESPONSE
