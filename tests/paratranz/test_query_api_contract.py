"""Offline HTTP contracts for the OpenAPI 0.5.7 query additions and corrections."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.api.paratranz_contribution_api import ParatranzScoresAPI
from transbridge.paratranz.api.paratranz_history_api import ParatranzHistoryAPI
from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI


class _Session(requests.Session):
    """Capture fully prepared requests without issuing any network traffic."""

    def __init__(self, payload, status=200):
        super().__init__()
        self.payload = payload
        self.status = status
        self.calls = []

    def send(self, request, **kwargs):
        self.calls.append(request)
        response = requests.Response()
        response.status_code = self.status
        response._content = json.dumps(self.payload).encode()
        response.headers["Content-Type"] = "application/json"
        response.request = request
        return response


def _api(cls, payload=None, *, status=200):
    session = _Session(payload if payload is not None else {"results": [], "rowCount": 0}, status)
    config = ParatranzConfig(
        token="synthetic-query-contract-token",
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    return cls(config=config, session=session), session


def _assert_request(session, path, params):
    assert len(session.calls) == 1
    request = session.calls[0]
    assert request.method == "GET"
    parsed = urlsplit(request.url)
    assert parsed.path == "/api" + path
    assert parse_qs(parsed.query, keep_blank_values=True) == params
    assert request.body is None


def test_user_projects_preserves_membership_and_nested_project_identity():
    payload = [{"id": 8, "uid": 17, "permission": 1, "privacy": 0, "project": {"id": 99, "name": "项目"}}]
    api, session = _api(ParatranzUserAPI, payload)
    assert api.get_user_projects(17) == payload
    _assert_request(session, "/users/17/projects", {})


def test_user_activities_uses_current_path_and_all_query_filters():
    payload = {"page": 2, "pageSize": 12, "rowCount": 1, "pageCount": 1, "results": [{"id": "c123"}]}
    api, session = _api(ParatranzUserAPI, payload)
    assert api.get_user_activities(17, 2, 12, operation="comment", begin="2026-01-01", end="2026-02-01") == payload
    _assert_request(
        session,
        "/users/17/activities",
        {"page": ["2"], "pageSize": ["12"], "operation": ["comment"], "begin": ["2026-01-01"], "end": ["2026-02-01"]},
    )


@pytest.mark.parametrize("tid,expected", [(11, ["11"]), ("11,12", ["11,12"]), ([11, 12], ["11", "12"])])
def test_project_history_supports_single_comma_and_repeated_ids_and_new_filters(tid, expected):
    api, session = _api(ParatranzHistoryAPI)
    api.get_project_history(4, 2, 10, 17, tid, "comment", begin="2026-01-01", end="2026-02-01", keyword="龙裔")
    _assert_request(
        session,
        "/projects/4/history",
        {
            "page": ["2"],
            "pageSize": ["10"],
            "uid": ["17"],
            "tid": expected,
            "type": ["comment"],
            "begin": ["2026-01-01"],
            "end": ["2026-02-01"],
            "keyword": ["龙裔"],
        },
    )


def test_file_history_serializes_delete_type_and_filename_time_filters():
    api, session = _api(ParatranzHistoryAPI)
    api.list_file_revisions(4, 3, 25, 8, "delete", filename="人物.json", begin="2026-01-01", end="2026-02-01")
    _assert_request(
        session,
        "/projects/4/files/revisions",
        {
            "page": ["3"],
            "pageSize": ["25"],
            "file": ["8"],
            "type": ["delete"],
            "filename": ["人物.json"],
            "begin": ["2026-01-01"],
            "end": ["2026-02-01"],
        },
    )


@pytest.mark.parametrize("mode", ["positional", "start_keyword", "begin_keyword", "matching_aliases"])
def test_scores_always_sends_begin_and_preserves_legacy_calls(mode):
    api, session = _api(ParatranzScoresAPI)
    if mode == "positional":
        api.get_scores(4, 2, 25, 17, "review", "2026-01-01", "2026-02-01")
    else:
        aliases = {
            "start_keyword": {"start": "2026-01-01"},
            "begin_keyword": {"begin": "2026-01-01"},
            "matching_aliases": {"start": "2026-01-01", "begin": "2026-01-01"},
        }[mode]
        api.get_scores(4, 2, 25, 17, "review", end="2026-02-01", **aliases)
    _assert_request(
        session,
        "/projects/4/scores",
        {
            "page": ["2"],
            "pageSize": ["25"],
            "uid": ["17"],
            "operation": ["review"],
            "begin": ["2026-01-01"],
            "end": ["2026-02-01"],
        },
    )


def test_conflicting_score_aliases_fail_before_request():
    api, session = _api(ParatranzScoresAPI)
    with pytest.raises(ValueError, match="start and begin"):
        api.get_scores(4, start="2026-01-01", begin="2026-02-01")
    assert not session.calls


@pytest.mark.parametrize(
    "cls,method,path",
    [
        (ParatranzUserAPI, "get_user_activities", "/users/4/activities"),
        (ParatranzHistoryAPI, "get_project_history", "/projects/4/history"),
        (ParatranzHistoryAPI, "list_file_revisions", "/projects/4/files/revisions"),
        (ParatranzScoresAPI, "get_scores", "/projects/4/scores"),
    ],
)
def test_omitted_filters_do_not_leak_into_query(cls, method, path):
    api, session = _api(cls)
    getattr(api, method)(4)
    _assert_request(session, path, {"page": ["1"], "pageSize": ["50"]})


@pytest.mark.parametrize(
    "cls,method",
    [
        (ParatranzUserAPI, "get_user_projects"),
        (ParatranzUserAPI, "get_user_activities"),
        (ParatranzHistoryAPI, "get_project_history"),
        (ParatranzHistoryAPI, "list_file_revisions"),
        (ParatranzScoresAPI, "get_scores"),
    ],
)
def test_cancelled_queries_do_not_send_requests(cls, method):
    api, session = _api(cls)
    cancellation = CancellationToken()
    cancellation._cancel("cancel query")
    with pytest.raises(TaskCancelled):
        getattr(api, method)(4, cancellation=cancellation)
    assert not session.calls


def test_user_projects_preserves_authorization_failure():
    api, session = _api(ParatranzUserAPI, {"message": "forbidden"}, status=403)
    with pytest.raises(ExternalServiceError) as captured:
        api.get_user_projects(17)
    assert captured.value.category == ExternalServiceCategory.AUTHORIZATION
    assert len(session.calls) == 1


def test_user_projects_rejects_non_list_response():
    api, _ = _api(ParatranzUserAPI, {"unexpected": "object"})
    with pytest.raises(ExternalServiceError) as captured:
        api.get_user_projects(17)
    assert captured.value.category == ExternalServiceCategory.INVALID_RESPONSE
