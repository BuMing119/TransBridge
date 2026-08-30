from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from typing import Any

import pytest

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI
from transbridge.paratranz.config_manager import ParatranzConfig
from transbridge.paratranz.paratranz_client import RetryPolicy

FIXTURES = Path(__file__).parent / "fixtures" / "terms"


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _TermsHandler(BaseHTTPRequestHandler):
    responses: deque[dict[str, Any]]
    calls: list[dict[str, Any]]

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self._respond()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self._respond()

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler contract
        self._respond()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler contract
        self._respond()

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.calls.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
        transcript = self.responses.popleft()
        response = transcript["response"]
        status = response["status"]
        response_body = response.get("body")
        encoded = b"" if response_body is None else json.dumps(response_body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        for key, value in response.get("headers", {}).items():
            self.send_header(key, str(value))
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        if encoded:
            self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        del format, args


@contextmanager
def _controlled_terms_http(fixtures: list[dict[str, Any]]):
    handler = type("ControlledTermsHandler", (_TermsHandler,), {"responses": deque(fixtures), "calls": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _api(base_url: str, *, attempts: int = 1) -> ParatranzTermsAPI:
    return ParatranzTermsAPI(
        config=ParatranzConfig(token="synthetic-contract-key", base_url=base_url, timeout=1),
        retry_policy=RetryPolicy(max_attempts=attempts, initial_backoff=0, maximum_backoff=0),
    )


def test_list_contract_records_path_query_wrapper_and_known_fields() -> None:
    first = _fixture("list-first-page.json")
    with _controlled_terms_http([first]) as (base_url, calls):
        payload = _api(base_url).list_terms(41, page=1, page_size=2)

    assert calls[0]["method"] == "GET"
    assert calls[0]["path"] == "/projects/41/terms?page=1&pageSize=2"
    record = payload["terms"][0]
    assert set(record) == {
        "id",
        "term",
        "translation",
        "variants",
        "caseSensitive",
        "pos",
        "note",
        "createdAt",
        "updatedAt",
    }
    assert payload["pagination"] == {"page": 1, "pageSize": 2, "totalPages": 3, "total": 5}


def test_write_contract_records_writable_body_and_empty_delete_semantics() -> None:
    create = _fixture("create-success.json")
    update = _fixture("update-success.json")
    delete = _fixture("delete-success.json")
    with _controlled_terms_http([create, update, delete]) as (base_url, calls):
        api = _api(base_url)
        created = api.create_term(41, create["request"]["body"])
        updated = api.update_term(41, 201, update["request"]["body"])
        deleted = api.delete_term(41, 201)

    assert created["id"] == updated["id"] == 201
    assert deleted is None
    assert [call["method"] for call in calls] == ["POST", "PUT", "DELETE"]
    assert json.loads(calls[0]["body"]) == create["request"]["body"]
    assert json.loads(calls[1]["body"]) == update["request"]["body"]
    assert calls[2]["body"] == b""


@pytest.mark.parametrize(
    ("fixture_name", "category"),
    [
        ("errors-401.json", ExternalServiceCategory.AUTHENTICATION),
        ("errors-403.json", ExternalServiceCategory.AUTHORIZATION),
        ("errors-404.json", ExternalServiceCategory.NOT_FOUND),
        ("errors-409.json", ExternalServiceCategory.CONFLICT),
        ("errors-429.json", ExternalServiceCategory.RATE_LIMITED),
        ("errors-5xx.json", ExternalServiceCategory.UNAVAILABLE),
    ],
)
def test_error_contract_preserves_typed_category_request_id_and_retry_metadata(
    fixture_name: str,
    category: ExternalServiceCategory,
) -> None:
    transcript = _fixture(fixture_name)
    with _controlled_terms_http([transcript]) as (base_url, calls):
        api = _api(base_url)
        request = transcript["request"]
        method = request["method"]
        with pytest.raises(ExternalServiceError) as captured:
            if method == "GET":
                api.list_terms(41)
            elif method == "POST":
                api.create_term(41, request["body"])
            else:
                api.update_term(41, 999 if category is ExternalServiceCategory.NOT_FOUND else 201, request["body"])

    assert len(calls) == 1
    assert captured.value.category is category
    assert captured.value.request_id == transcript["response"]["headers"]["X-Request-ID"]
    if category is ExternalServiceCategory.RATE_LIMITED:
        assert captured.value.retry_after == 2


def test_terms_api_forwards_cancellation_and_never_starts_a_cancelled_request() -> None:
    token = CancellationToken()
    token._cancel("cancel terms contract")
    transcript = _fixture("list-last-page.json")
    with _controlled_terms_http([transcript]) as (base_url, calls):
        with pytest.raises(TaskCancelled):
            _api(base_url).list_terms(41, cancellation=token)
    assert calls == []


def test_update_does_not_blindly_retry_after_an_unknown_write_failure() -> None:
    unavailable = _fixture("errors-5xx.json")
    unavailable["request"] = {
        "method": "PUT",
        "path": "/projects/41/terms/201",
        "body": {"term": "Dragonborn", "translation": "龙裔"},
    }
    success = _fixture("update-success.json")
    with _controlled_terms_http([unavailable, success]) as (base_url, calls):
        with pytest.raises(ExternalServiceError) as captured:
            _api(base_url, attempts=2).update_term(41, 201, unavailable["request"]["body"])

    assert captured.value.category is ExternalServiceCategory.UNAVAILABLE
    assert len(calls) == 1


def test_contract_fixture_set_is_complete_synthetic_and_secret_free() -> None:
    required = {
        "list-first-page.json",
        "list-middle-page.json",
        "list-last-page.json",
        "create-success.json",
        "update-success.json",
        "delete-success.json",
        "errors-401.json",
        "errors-403.json",
        "errors-404.json",
        "errors-409.json",
        "errors-429.json",
        "errors-5xx.json",
        "unknown-fields.json",
        "missing-id.json",
        "duplicate-id.json",
        "snapshot-changed-between-pages.json",
        "timeout-after-write.json",
        "sync-scenarios.json",
    }
    assert required <= {path.name for path in FIXTURES.glob("*.json")}

    forbidden_keys = {"authorization", "cookie", "token", "email"}

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(str(key).casefold() for key in value)
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
        elif isinstance(value, str):
            assert "@" not in value
            assert "Bearer " not in value

    for filename in required:
        inspect(_fixture(filename))


def test_sync_calibration_and_unknown_outcome_are_explicit_contracts() -> None:
    sync = _fixture("sync-scenarios.json")
    assert sync["calibration"] == {
        "pluginScope": "lossy_skip",
        "activeVariantPerTarget": 1,
        "deletePolicy": "managed_baseline_only",
    }
    assert len(sync["scenarios"]) == 11
    timeout = _fixture("timeout-after-write.json")
    assert timeout["transport"]["serverApplied"] == "unknown"
    assert timeout["expected"] == {"status": "unknown", "automaticRetry": False, "reconcile": True}


@pytest.mark.integration
def test_live_terms_list_smoke_requires_explicit_dedicated_project() -> None:
    token = os.getenv("TRANSBRIDGE_PARATRANZ_CONTRACT_TOKEN")
    project_id = os.getenv("TRANSBRIDGE_PARATRANZ_CONTRACT_PROJECT_ID")
    if not token or not project_id:
        pytest.skip("dedicated ParaTranz contract token/project were not explicitly configured")
    api = ParatranzTermsAPI(token=token)
    payload = api.list_terms(int(project_id), page=1, page_size=1)
    assert isinstance(payload, (list, dict))
