"""Offline wire contracts for the ParaTranz OpenAPI 0.7.0 additions."""

from email import policy
from email.parser import BytesParser
from io import BytesIO
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import BaseAdapter

from transbridge.paratranz.api import (
    ParatranzFileLabelsAPI,
    ParatranzFilesAPI,
    ParatranzHistoryAPI,
    ParatranzIssuesAPI,
    ParatranzMailsAPI,
    ParatranzMembersAPI,
    ParatranzProjectAPI,
    ParatranzStringsAPI,
    ParatranzSuggestionsAPI,
    ParatranzTasksAPI,
    ParatranzTermsAPI,
)


class _Wire(BaseAdapter):
    def __init__(self, *responses):
        self.pending = iter(responses)
        self.requests = []

    def send(self, request, **kwargs):
        status, body, headers = next(self.pending)
        response = requests.Response()
        response.status_code = status
        response.headers.update(headers)
        response.url = request.url
        response.request = request
        response.raw = BytesIO(body)
        self.requests.append(request)
        return response

    def close(self):
        pass


@pytest.fixture
def wire_api():
    with requests.Session() as session:

        def build(cls, payload=None, *, status=200, responses=None):
            if responses is None:
                body = b"" if status == 204 else json.dumps(payload).encode()
                responses = [(status, body, {})]
            api = cls(token="offline-070-contract", session=session)
            wire = _Wire(*responses)
            session.mount("https://", wire)
            return api, wire

        yield build


def _assert_request(wire, method, path, *, params=None, payload=None):
    request = wire.requests[0]
    location = urlsplit(request.url)
    assert request.method == method
    assert location.path == f"/api{path}"
    assert parse_qs(location.query, keep_blank_values=True) == (params or {})
    if payload is not None:
        assert json.loads(request.body) == payload
    else:
        assert request.body is None


@pytest.mark.parametrize(
    ("cls", "operation", "args", "options", "method", "path", "body", "response"),
    [
        (
            ParatranzProjectAPI,
            "transfer_project",
            (9, 17),
            {"keep": True},
            "POST",
            "/projects/9",
            {"op": "transfer", "params": {"target": 17, "keep": True}},
            None,
        ),
        (
            ParatranzProjectAPI,
            "save_tag_rules",
            (9, []),
            {"config": {}},
            "POST",
            "/projects/9/tag-rules",
            {"rules": [], "config": {}},
            None,
        ),
        (
            ParatranzFilesAPI,
            "rename_folder",
            (9, "old/", "新/"),
            {},
            "PUT",
            "/projects/9/files",
            {"op": "renameFolder", "source": "old/", "target": "新/"},
            None,
        ),
        (
            ParatranzFilesAPI,
            "merge_files",
            (9, 4, 5, 0),
            {},
            "PUT",
            "/projects/9/files",
            {"op": "mergeFiles", "source": 4, "target": 5, "mode": 0},
            {"source": {"id": 4}, "target": {"id": 5}},
        ),
        (
            ParatranzFileLabelsAPI,
            "list_file_labels",
            (9,),
            {},
            "GET",
            "/projects/9/files/labels",
            None,
            [{"id": 7, "fileIds": [4]}],
        ),
        (
            ParatranzFileLabelsAPI,
            "create_file_label",
            (9, "校对"),
            {"file_ids": [4], "color": "#ffaa11"},
            "POST",
            "/projects/9/files/labels",
            {"name": "校对", "fileIds": [4], "color": "#ffaa11"},
            {"id": 7},
        ),
        (
            ParatranzFileLabelsAPI,
            "get_file_label",
            (9, 7),
            {},
            "GET",
            "/projects/9/files/labels/7",
            None,
            {"id": 7, "fileIds": [4]},
        ),
        (
            ParatranzFileLabelsAPI,
            "update_file_label",
            (9, 7),
            {"file_ids": []},
            "PUT",
            "/projects/9/files/labels/7",
            {"fileIds": []},
            {"id": 7, "fileIds": []},
        ),
        (ParatranzFileLabelsAPI, "delete_file_label", (9, 7), {}, "DELETE", "/projects/9/files/labels/7", None, None),
        (
            ParatranzHistoryAPI,
            "get_file_revision",
            (9, 8),
            {},
            "GET",
            "/projects/9/files/revisions/8",
            None,
            {"id": 8, "canRevert": True},
        ),
        (
            ParatranzHistoryAPI,
            "revert_file_revision",
            (9, 8),
            {},
            "POST",
            "/projects/9/files/revisions/8",
            {"op": "revert"},
            {"id": 10},
        ),
        (
            ParatranzHistoryAPI,
            "delete_file_revision",
            (9, 8),
            {},
            "DELETE",
            "/projects/9/files/revisions/8",
            None,
            None,
        ),
        (ParatranzMembersAPI, "leave_project", (9,), {}, "DELETE", "/projects/9/members/self", None, None),
        (
            ParatranzTasksAPI,
            "trigger_task_export",
            (9, 6, "translation"),
            {},
            "POST",
            "/projects/9/tasks/6/artifacts",
            {"kind": "translation"},
            {"id": 27, "status": 0},
        ),
        (
            ParatranzMembersAPI,
            "add_member",
            (9, 10, 1),
            {"note": ""},
            "POST",
            "/projects/9/members",
            {"uid": 10, "permission": 1, "note": ""},
            {"id": 40},
        ),
    ],
)
def test_admin_and_task_operations(wire_api, cls, operation, args, options, method, path, body, response):
    responses = [(200, b"", {})] if response is None else None
    api, wire = wire_api(cls, response, responses=responses)
    assert getattr(api, operation)(*args, **options) == response
    _assert_request(wire, method, path, payload=body)


@pytest.mark.parametrize(
    ("cls", "operation", "args", "options", "method", "path", "params", "result"),
    [
        (
            ParatranzProjectAPI,
            "get_tag_rules",
            (9,),
            {"predefined": True},
            "GET",
            "/projects/9/tag-rules",
            {"predefined": ["1"]},
            {"rules": [], "predefined": [{"name": "sample"}]},
        ),
        (
            ParatranzStringsAPI,
            "get_string_terms",
            (9, 6),
            {"force": True},
            "GET",
            "/projects/9/strings/6/terms",
            {"force": ["true"]},
            [{"id": 5, "match": "book"}],
        ),
        (
            ParatranzTasksAPI,
            "get_task_artifact",
            (9, 6, "source"),
            {},
            "GET",
            "/projects/9/tasks/6/artifacts",
            {"kind": ["source"]},
            None,
        ),
        (
            ParatranzProjectAPI,
            "list_projects",
            (),
            {
                "uid": 14,
                "game": "json",
                "stage": 0,
                "public": True,
                "filter_text": "模组",
                "source": "en",
                "target": "zh",
            },
            "GET",
            "/projects",
            {
                "page": ["1"],
                "pageSize": ["50"],
                "uid": ["14"],
                "game": ["json"],
                "stage": ["0"],
                "public": ["1"],
                "filter": ["模组"],
                "source": ["en"],
                "target": ["zh"],
            },
            {"results": [{"id": 9}]},
        ),
        (
            ParatranzFilesAPI,
            "list_files",
            (9,),
            {
                "order_by": "-name",
                "name": "mod",
                "size": 5,
                "lightweight": True,
                "task": 6,
                "created_at": "2026-11-14",
                "modified_at": "2026-11-15",
            },
            "GET",
            "/projects/9/files",
            {
                "orderBy": ["-name"],
                "name": ["mod"],
                "size": ["5"],
                "list": ["1"],
                "task": ["6"],
                "createdAt": ["2026-11-14"],
                "modifiedAt": ["2026-11-15"],
            },
            [{"id": 7}],
        ),
        (
            ParatranzTermsAPI,
            "list_terms",
            (9,),
            {"text": "abc", "term": "key", "term_id": 5, "order_by": "-term"},
            "GET",
            "/projects/9/terms",
            {"page": ["1"], "pageSize": ["50"], "text": ["abc"], "term": ["key"], "id": ["5"], "orderBy": ["-term"]},
            {"results": [{"id": 5}]},
        ),
        (
            ParatranzHistoryAPI,
            "get_term_history",
            (9, 7, 2, 100),
            {},
            "GET",
            "/projects/9/terms/7/history",
            {},
            [{"id": 14}],
        ),
        (
            ParatranzMembersAPI,
            "list_members",
            (9,),
            {"begin": "2026-11-14", "end": "2026-11-15"},
            "GET",
            "/projects/9/members",
            {"begin": ["2026-11-14"], "end": ["2026-11-15"]},
            [{"id": 15}],
        ),
        (
            ParatranzMembersAPI,
            "delete_member",
            (9, 15),
            {"clean": True},
            "DELETE",
            "/projects/9/members/15",
            {"clean": ["1"]},
            None,
        ),
        (
            ParatranzIssuesAPI,
            "list_issues",
            (9, 0),
            {"page": 2, "page_size": 25, "uid": 7},
            "GET",
            "/projects/9/issues",
            {"status": ["0"], "page": ["2"], "pageSize": ["25"], "uid": ["7"]},
            {"page": 2, "results": [{"id": 5}]},
        ),
        (
            ParatranzMailsAPI,
            "get_conversation",
            (14,),
            {"before": 71, "limit": 100},
            "GET",
            "/mails/conversations/14",
            {"before": ["71"], "limit": ["100"]},
            [{"id": 70}],
        ),
        (
            ParatranzTasksAPI,
            "list_tasks",
            (9,),
            {"lightweight": True, "name": "翻译", "ids": [4, 6]},
            "GET",
            "/projects/9/tasks",
            {"list": ["1"], "name": ["翻译"], "id": ["4", "6"]},
            [{"id": 6, "status": 0}],
        ),
    ],
)
def test_query_options(wire_api, cls, operation, args, options, method, path, params, result):
    api, wire = wire_api(cls, result)
    assert getattr(api, operation)(*args, **options) == result
    _assert_request(wire, method, path, params=params)


def test_string_filters_preserve_repeated_ids_operators_and_anchor(wire_api):
    api, wire = wire_api(ParatranzStringsAPI, {"page": 3, "results": [{"id": 6}]})
    assert (
        api.list_strings(
            9,
            file=[4, 5],
            stage=0,
            detailed=True,
            task=8,
            uid=[2, "null"],
            exclude_uid=[8],
            filename="quest",
            file_label=[2, 3],
            exclude_file_label=[5],
            translate_by=[2],
            exclude_translate_by=[3],
            edit_by=[4],
            exclude_edit_by=[5],
            review_by=[6],
            exclude_review_by=[7],
            anchor=6,
            mode=5,
            ids=[5, 6],
            order_by="-updatedAt",
            words=0,
            words_less_than=12,
            length_gt="char",
            filters={"text": "森林", "original~=": "^Hello", "updatedAt>=": "2026-11-16"},
        )["page"]
        == 3
    )
    _assert_request(
        wire,
        "GET",
        "/projects/9/strings",
        params={
            "pageSize": ["50"],
            "file": ["4", "5"],
            "stage": ["0"],
            "detailed": ["1"],
            "task": ["8"],
            "uid": ["2", "null"],
            "uid!": ["8"],
            "filename": ["quest"],
            "fileLabel": ["2", "3"],
            "fileLabel!": ["5"],
            "translateBy": ["2"],
            "translateBy!": ["3"],
            "editBy": ["4"],
            "editBy!": ["5"],
            "reviewBy": ["6"],
            "reviewBy!": ["7"],
            "anchor": ["6"],
            "mode": ["5"],
            "id": ["5", "6"],
            "orderBy": ["-updatedAt"],
            "words": ["0"],
            "words<": ["12"],
            "lengthGt": ["char"],
            "text": ["森林"],
            "original~=": ["^Hello"],
            "updatedAt>=": ["2026-11-16"],
        },
    )


def test_explicit_page_takes_priority_over_string_anchor(wire_api):
    api, wire = wire_api(ParatranzStringsAPI, {"page": 1, "results": []})
    api.list_strings(9, 1, file=4, anchor=6)
    _assert_request(
        wire,
        "GET",
        "/projects/9/strings",
        params={
            "page": ["1"],
            "pageSize": ["50"],
            "file": ["4"],
            "anchor": ["6"],
        },
    )


@pytest.mark.parametrize("payload", [None, {"result": "译文", "service": "baidu"}])
def test_machine_translation_serializes_supported_options(wire_api, payload):
    api, wire = wire_api(ParatranzSuggestionsAPI, payload)
    assert (
        api.get_machine_translation(
            9,
            6,
            service="baidu",
            api_key="synthetic-api",
            app_key="synthetic-app",
            app_secret="synthetic secret+",
            force=True,
            model="synthetic",
            is_pro=False,
            action="synthetic",
        )
        == payload
    )
    _assert_request(
        wire,
        "GET",
        "/projects/9/strings/6/suggestions/mt",
        params={
            "service": ["baidu"],
            "apiKey": ["synthetic-api"],
            "appKey": ["synthetic-app"],
            "appSecret": ["synthetic secret+"],
            "force": ["true"],
            "model": ["synthetic"],
            "isPro": ["false"],
            "action": ["synthetic"],
        },
    )


def test_task_artifact_download_follows_redirect_and_streams(wire_api, tmp_path):
    url = "https://files.example.net/task-export.zip"
    api, wire = wire_api(
        ParatranzTasksAPI,
        responses=[
            (302, b"", {"Location": url}),
            (200, b"archive", {}),
        ],
    )
    destination = tmp_path / "task.zip"
    assert api.download_task_artifact(9, 6, "translation", str(destination)) == str(destination)
    assert destination.read_bytes() == b"archive"
    _assert_request(wire, "GET", "/projects/9/tasks/6/artifacts/download", params={"kind": ["translation"]})
    assert wire.requests[1].url == url
    assert "Authorization" not in wire.requests[1].headers


def test_json_file_create_update_and_import(wire_api):
    items = [{"key": "greeting", "original": "Hello"}]
    api, wire = wire_api(ParatranzFilesAPI, status=204)
    assert (
        api.create_file_from_items(9, "new.json", items, file_format="json", task=6, file_hash="existing", extra={})
        is None
    )
    _assert_request(
        wire,
        "POST",
        "/projects/9/files",
        payload={
            "filename": "new.json",
            "items": items,
            "format": "json",
            "task": 6,
            "hash": "existing",
            "extra": {},
        },
    )
    api, wire = wire_api(ParatranzFilesAPI, {"status": "hashMatched"})
    assert api.update_file_from_items(
        9, 4, "new.json", items, task=6, file_hash="existing", incremental=False, skip=True
    ) == {"status": "hashMatched"}
    _assert_request(
        wire,
        "POST",
        "/projects/9/files/4",
        payload={
            "filename": "new.json",
            "items": items,
            "task": 6,
            "hash": "existing",
            "incremental": False,
            "skip": True,
        },
    )
    api, wire = wire_api(ParatranzFilesAPI, {"status": "unchanged"})
    translations = [{"key": "greeting", "translation": "你好"}]
    assert api.import_translations(9, 4, "new.json", translations, file_hash="existing") == {"status": "unchanged"}
    _assert_request(
        wire,
        "POST",
        "/projects/9/files/4/translation",
        payload={
            "filename": "new.json",
            "items": translations,
            "hash": "existing",
            "force": False,
            "skip": False,
        },
    )


def _multipart(wire, path, *, method="POST"):
    request = wire.requests[0]
    assert request.method == method and urlsplit(request.url).path == f"/api{path}"
    headers = f"Content-Type: {request.headers['Content-Type']}\r\n\r\n".encode()
    message = BytesParser(policy=policy.default).parsebytes(headers + request.body)
    return {
        part.get_param("name", header="Content-Disposition"): part.get_payload(decode=True)
        for part in message.iter_parts()
    }


def test_multipart_task_and_translation_import_metadata(wire_api, tmp_path: Path):
    document = tmp_path / "import.json"
    document.write_bytes(b"[]")
    api, wire = wire_api(ParatranzFilesAPI, {"file": {"id": 4}})
    api.upload_file(9, str(document), path="quests/", task=6)
    assert _multipart(wire, "/projects/9/files") == {"file": b"[]", "path": b"quests/", "task": b"6"}
    api, wire = wire_api(ParatranzFilesAPI, {"file": {"id": 4}})
    api.reupload_file(9, 4, str(document), task=6, incremental=True)
    assert _multipart(wire, "/projects/9/files/4") == {
        "file": b"[]",
        "task": b"6",
        "incremental": b"true",
        "skip": b"false",
    }
    api, wire = wire_api(ParatranzFilesAPI, {"status": "unchanged"})
    api.update_file_translation(9, 4, str(document), filename="history.json", path="quests/", skip=True)
    assert _multipart(wire, "/projects/9/files/4/translation") == {
        "file": b"[]",
        "filename": b"history.json",
        "path": b"quests/",
        "force": b"false",
        "skip": b"true",
    }


def test_term_overwrite_is_explicit(wire_api, tmp_path):
    csv = tmp_path / "terms.csv"
    csv.write_bytes(b"term,translation\n")
    for overwrite in (False, True):
        api, wire = wire_api(ParatranzTermsAPI, {"updated": 1})
        api.import_terms(9, str(csv), overwrite=overwrite)
        assert _multipart(wire, "/projects/9/terms", method="PUT") == {"file": b"term,translation\n"}
        assert urlsplit(wire.requests[0].url).query == ("overwrite=1" if overwrite else "")
