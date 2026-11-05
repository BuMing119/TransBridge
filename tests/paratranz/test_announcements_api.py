"""公告 OpenAPI 契约：真实 requests 序列化，内存适配器不访问远端。"""

from collections import deque
import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.api.paratranz_announcements_api import ParatranzAnnouncementsAPI
from transbridge.paratranz.paratranz_client import RetryPolicy


class _Responses(BaseAdapter):
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        status, payload = self.responses.popleft()
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = status
        response.headers["Content-Type"] = "application/json"
        response._content = b"" if payload is None else json.dumps(payload).encode()
        return response

    def close(self):
        pass


def _api(*responses):
    adapter = _Responses(*responses)
    session = requests.Session()
    session.mount("http://", adapter)
    api = ParatranzAnnouncementsAPI(
        config=ParatranzConfig(
            token="synthetic-announcements-key",
            base_url="http://paratranz.test/api",
            credential_store=UnavailableCredentialStore(),
            environment={},
        ),
        session=session,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff=0, maximum_backoff=0),
    )
    return api, adapter.requests


@pytest.mark.parametrize("bumped", [None, False, True])
def test_list_preserves_pagination_filters_and_official_envelope(bumped):
    payload = {"page": 2, "pageSize": 7, "rowCount": 1, "pageCount": 1, "results": [{"id": 9}]}
    api, calls = _api((200, payload))
    assert api.list_announcements(41, 2, 7, bumped=bumped, category="公告分类") == payload
    request = calls[0]
    assert request.method == "GET"
    assert urlsplit(request.url).path == "/api/projects/41/announcements"
    query = parse_qs(urlsplit(request.url).query)
    assert query == {
        "page": ["2"],
        "pageSize": ["7"],
        "category": ["公告分类"],
        **({"bumped": [str(bumped).lower()]} if bumped is not None else {}),
    }


def test_list_default_query_omits_unspecified_filters():
    api, calls = _api((200, {"results": []}))
    api.list_announcements(41)
    assert parse_qs(urlsplit(calls[0].url).query) == {"page": ["1"], "pageSize": ["50"]}


@pytest.mark.parametrize("operation,method", [("create_announcement", "POST"), ("update_announcement", "PUT")])
def test_write_serializes_false_fields_without_changing_input(operation, method):
    data = {"title": "公告", "content": "**说明**", "bumped": False, "internal": False}
    original = data.copy()
    payload = {"id": 9, **data, "html": "<p><strong>说明</strong></p>"}
    api, calls = _api((200, payload))
    args = (41, data) if method == "POST" else (41, 9, data)
    assert getattr(api, operation)(*args) == payload
    assert data == original
    assert calls[0].method == method
    assert calls[0].url == "http://paratranz.test/api/projects/41/announcements" + ("/9" if method == "PUT" else "")
    assert json.loads(calls[0].body) == data


def test_create_transmits_optional_category_and_update_omits_unspecified_fields():
    api, calls = _api((200, {"id": 9}), (200, {"id": 9}))
    api.create_announcement(41, {"title": "通知", "content": "内容", "category": "release"})
    api.update_announcement(41, 9, {"internal": False})
    assert json.loads(calls[0].body)["category"] == "release"
    assert json.loads(calls[1].body) == {"internal": False}


def test_get_and_delete_use_item_path_and_preserve_empty_delete_response():
    payload = {"id": 9, "title": "通知", "bumpedAt": None, "internal": False}
    api, calls = _api((200, payload), (200, None))
    assert api.get_announcement(41, 9) == payload
    assert api.delete_announcement(41, 9) is None
    assert [call.method for call in calls] == ["GET", "DELETE"]
    assert all(call.url == "http://paratranz.test/api/projects/41/announcements/9" for call in calls)
    assert all(call.body is None for call in calls)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_detail_permission_errors_are_typed(status):
    api, calls = _api((status, {"message": "not allowed"}))
    with pytest.raises(ExternalServiceError) as caught:
        api.get_announcement(41, 9)
    assert caught.value.status == status
    assert len(calls) == 1


@pytest.mark.parametrize(
    "operation,args",
    [
        ("list_announcements", (41,)),
        ("create_announcement", (41, {"title": "通知", "content": "内容"})),
        ("get_announcement", (41, 9)),
        ("update_announcement", (41, 9, {"internal": False})),
        ("delete_announcement", (41, 9)),
    ],
)
def test_all_operations_honor_cancellation_before_network(operation, args):
    api, calls = _api()
    token = CancellationToken()
    token._cancel("cancel announcement operation")
    with pytest.raises(TaskCancelled):
        getattr(api, operation)(*args, cancellation=token)
    assert calls == []


@pytest.mark.parametrize(
    "operation,args",
    [
        ("create_announcement", (41, {"title": "通知", "content": "内容"})),
        ("update_announcement", (41, 9, {"bumped": True})),
        ("delete_announcement", (41, 9)),
    ],
)
def test_unknown_write_outcome_is_not_retried(operation, args):
    api, calls = _api((503, {"message": "unavailable"}), (200, {"id": 9}))
    with pytest.raises(ExternalServiceError) as caught:
        getattr(api, operation)(*args)
    assert caught.value.category is ExternalServiceCategory.UNAVAILABLE
    assert len(calls) == 1


def test_list_rejects_wrong_response_shape():
    api, _ = _api((200, []))
    with pytest.raises(ExternalServiceError) as caught:
        api.list_announcements(41)
    assert caught.value.category is ExternalServiceCategory.INVALID_RESPONSE
