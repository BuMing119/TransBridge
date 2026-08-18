from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from unittest.mock import MagicMock

import pytest

from transbridge.application.ports.paratranz import (
    ExternalServiceCategory,
    ExternalServiceError,
    ParaTranzEntry,
)
from transbridge.application.tasks import CancellationToken, TaskCancelled
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.api.paratranz_export_api import ParatranzExportAPI
from transbridge.paratranz.api.paratranz_files_api import ParatranzFilesAPI
from transbridge.paratranz.paratranz_client import ParatranzClient, RetryPolicy
from transbridge.paratranz.service import ParaTranzService


class _Handler(BaseHTTPRequestHandler):
    responses: deque[dict]
    calls: list[dict]

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        self._respond()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        self._respond()

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.calls.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
        item = self.responses.popleft() if self.responses else {"status": 500, "json": {}}
        if item.get("delay"):
            time.sleep(item["delay"])
        if item.get("disconnect"):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        payload = item.get("body")
        if payload is None:
            payload = json.dumps(item.get("json", {})).encode()
        self.send_response(item.get("status", 200))
        self.send_header("Content-Type", "application/json")
        for key, value in item.get("headers", {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, format: str, *args) -> None:
        del format, args


@contextmanager
def _controlled_http(responses: list[dict]):
    handler = type(
        "ControlledHandler",
        (_Handler,),
        {"responses": deque(responses), "calls": []},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler.calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _client(base_url: str, *, attempts: int = 4, timeout: float = 1) -> ParatranzClient:
    config = ParatranzConfig(
        token="pt-typed-secret-0123456789",
        base_url=base_url,
        timeout=timeout,
        credential_store=UnavailableCredentialStore(),
        environment={},
    )
    return ParatranzClient(
        config=config,
        retry_policy=RetryPolicy(
            max_attempts=attempts,
            initial_backoff=0.01,
            maximum_backoff=0.05,
        ),
    )


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, ExternalServiceCategory.AUTHENTICATION),
        (403, ExternalServiceCategory.AUTHORIZATION),
        (404, ExternalServiceCategory.NOT_FOUND),
        (409, ExternalServiceCategory.CONFLICT),
        (429, ExternalServiceCategory.RATE_LIMITED),
        (503, ExternalServiceCategory.UNAVAILABLE),
    ],
)
def test_statuses_map_to_typed_secret_free_errors(status: int, category: ExternalServiceCategory) -> None:
    canary = "pt-typed-secret-0123456789"
    with _controlled_http([
        {
            "status": status,
            "headers": {"X-Request-ID": "remote-request-7", "Retry-After": "0.01"},
            "body": f'{{"token":"{canary}"}}'.encode(),
        }
    ]) as (base_url, calls):
        with pytest.raises(ExternalServiceError) as captured:
            _client(base_url, attempts=1)._request("GET", "/projects")

    error = captured.value
    assert error.category is category
    assert error.status == status
    assert error.request_id == "remote-request-7"
    assert canary not in str(error)
    assert len(calls) == 1


def test_get_retries_with_bounded_retry_after_and_correlation() -> None:
    with _controlled_http([
        {"status": 429, "headers": {"Retry-After": "999"}},
        {"status": 503},
        {"status": 200, "json": {"projects": []}},
    ]) as (base_url, calls):
        started = time.monotonic()
        result = _client(base_url)._request("GET", "/projects", expected_type=dict)
        elapsed = time.monotonic() - started

    assert result == {"projects": []}
    assert len(calls) == 3
    assert elapsed < 0.5
    request_ids = {call["headers"]["X-Request-ID"] for call in calls}
    assert len(request_ids) == 1


def test_non_idempotent_post_has_zero_retry() -> None:
    with _controlled_http([{"status": 503}, {"status": 200, "json": {"id": 1}}]) as (base_url, calls):
        with pytest.raises(ExternalServiceError) as captured:
            _client(base_url)._request("POST", "/projects", json={"name": "x"})

    assert captured.value.category is ExternalServiceCategory.UNAVAILABLE
    assert len(calls) == 1


def test_idempotency_key_allows_bounded_write_retry() -> None:
    with _controlled_http([{"status": 503}, {"status": 200, "json": {"id": 1}}]) as (base_url, calls):
        result = _client(base_url)._request(
            "POST",
            "/projects",
            json={"name": "x"},
            idempotency_key="operation-1",
            expected_type=dict,
        )

    assert result == {"id": 1}
    assert len(calls) == 2
    assert all(call["headers"]["Idempotency-Key"] == "operation-1" for call in calls)


def test_malformed_json_is_a_typed_non_retryable_error() -> None:
    with _controlled_http([{"status": 200, "body": b"{"}]) as (base_url, calls):
        with pytest.raises(ExternalServiceError) as captured:
            _client(base_url)._request("GET", "/projects")

    assert captured.value.category is ExternalServiceCategory.INVALID_RESPONSE
    assert len(calls) == 1


def test_timeout_and_disconnect_retry_only_to_bound() -> None:
    with _controlled_http([{"delay": 0.1}] * 3) as (base_url, calls):
        with pytest.raises(ExternalServiceError) as timeout_error:
            _client(base_url, attempts=2, timeout=0.02)._request("GET", "/projects")
        assert timeout_error.value.category is ExternalServiceCategory.TIMEOUT
        assert len(calls) == 2

    with _controlled_http([{"disconnect": True}] * 3) as (base_url, calls):
        with pytest.raises(ExternalServiceError) as transport_error:
            _client(base_url, attempts=2)._request("GET", "/projects")
        assert transport_error.value.category is ExternalServiceCategory.TRANSPORT
        assert len(calls) == 2


class _CancelAfterResponse:
    def __init__(self) -> None:
        self.checks = 0

    @property
    def is_cancelled(self) -> bool:
        return self.checks >= 2

    def wait(self, timeout: float | None = None) -> bool:
        del timeout
        return self.is_cancelled

    def raise_if_cancelled(self) -> None:
        self.checks += 1
        if self.checks >= 2:
            raise TaskCancelled("cancelled after response")


def test_cancellation_before_request_during_backoff_and_after_response() -> None:
    token = CancellationToken()
    token._cancel("before request")
    with _controlled_http([{"status": 200}]) as (base_url, calls):
        with pytest.raises(TaskCancelled):
            _client(base_url)._request("GET", "/projects", cancellation=token)
        assert calls == []

    token = CancellationToken()
    with _controlled_http([{"status": 429, "headers": {"Retry-After": "1"}}]) as (
        base_url,
        calls,
    ):
        timer = threading.Timer(0.02, token._cancel, args=("during backoff",))
        timer.start()
        started = time.monotonic()
        with pytest.raises(TaskCancelled):
            _client(base_url)._request("GET", "/projects", cancellation=token)
        timer.join(timeout=1)
        assert time.monotonic() - started < 0.3
        assert len(calls) == 1

    with _controlled_http([{"status": 200, "json": {"projects": []}}]) as (
        base_url,
        calls,
    ):
        with pytest.raises(TaskCancelled, match="after response"):
            _client(base_url)._request("GET", "/projects", cancellation=_CancelAfterResponse())
        assert len(calls) == 1


def test_service_maps_tool_operations_to_real_endpoint_methods() -> None:
    projects = MagicMock()
    strings = MagicMock()
    history = MagicMock()
    exports = MagicMock()
    service = ParaTranzService(projects, strings, history, exports)
    strings.list_strings.return_value = {
        "results": [
            {
                "id": 7,
                "key": "NPC_:1",
                "original": "Hello",
                "translation": "",
                "context": "NPC_:FULL",
                "stage": 0,
            }
        ]
    }
    strings.update_string.return_value = {
        "id": 7,
        "key": "NPC_:1",
        "original": "Hello",
        "translation": "你好",
        "context": "NPC_:FULL",
        "stage": 1,
    }
    history.list_file_revisions.return_value = {"results": [{"id": 3, "status": "success", "filename": "a.json"}]}
    entry = ParaTranzEntry(None, "NPC_:1", "Hello", "你好", "NPC_:FULL", 1)

    result = service.upsert_entry(11, entry)
    revisions = service.list_upload_history(11, limit=20)

    assert result.remote_id == 7
    assert revisions[0].revision_id == 3
    strings.list_strings.assert_called_once()
    strings.update_string.assert_called_once_with(11, 7, entry.to_remote_payload(), cancellation=None)
    history.list_file_revisions.assert_called_once()
    assert not hasattr(service, "get_entries")
    assert not hasattr(service, "get_upload_history")

    strings.list_strings.return_value = {"results": []}
    strings.create_string.return_value = {
        "id": 8,
        **entry.to_remote_payload(),
    }
    created = service.upsert_entry(11, entry)
    assert created.remote_id == 8
    strings.create_string.assert_called_once_with(11, entry.to_remote_payload(), cancellation=None)


def test_binary_download_uses_typed_retry_and_cancellation_boundary(tmp_path) -> None:
    with _controlled_http([{"status": 503}, {"status": 200, "body": b"binary-artifact"}]) as (base_url, calls):
        config = _client(base_url).config
        api = ParatranzExportAPI(
            config=config,
            retry_policy=RetryPolicy(max_attempts=2, initial_backoff=0.01, maximum_backoff=0.01),
        )
        target = tmp_path / "artifact.zip"
        assert api.download_artifacts(7, str(target)) == str(target)
        assert target.read_bytes() == b"binary-artifact"
        assert len(calls) == 2

    token = CancellationToken()
    token._cancel("cancel binary download")
    with _controlled_http([{"status": 200, "body": b"never"}]) as (base_url, calls):
        config = _client(base_url).config
        with pytest.raises(TaskCancelled):
            ParatranzExportAPI(config=config).download_artifacts(7, str(tmp_path / "cancelled.zip"), cancellation=token)
        assert calls == []


def test_translation_upload_uses_typed_non_idempotent_boundary(tmp_path) -> None:
    upload = tmp_path / "upload.json"
    upload.write_text("[]", encoding="utf-8")
    with _controlled_http([{"status": 409}, {"status": 200, "json": {}}]) as (
        base_url,
        calls,
    ):
        config = _client(base_url).config
        with pytest.raises(ExternalServiceError) as captured:
            ParatranzFilesAPI(config=config).update_file_translation(7, 8, str(upload))
    assert captured.value.category is ExternalServiceCategory.CONFLICT
    assert len(calls) == 1
