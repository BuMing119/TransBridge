"""Offline HTTP error contracts from ParaTranz OpenAPI 0.7.0."""

import json
import logging
from unittest.mock import MagicMock
from urllib.parse import quote, quote_plus

import pytest
import requests

from transbridge.application.ports.paratranz import ExternalServiceCategory, ExternalServiceError
from transbridge.application.security.diagnostic_logging import DiagnosticLogFormatter
from transbridge.config.paratranz import ParatranzConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.paratranz.paratranz_client import ParatranzClient, RetryPolicy


@pytest.fixture
def client() -> ParatranzClient:
    return ParatranzClient(
        config=ParatranzConfig(
            token="synthetic-paratranz-token",
            base_url="https://paratranz.invalid/api",
            credential_store=UnavailableCredentialStore(),
            environment={},
        ),
        retry_policy=RetryPolicy(max_attempts=4, initial_backoff=0, maximum_backoff=0),
    )


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (302, ExternalServiceCategory.INVALID_RESPONSE),
        (400, ExternalServiceCategory.INVALID_REQUEST),
        (405, ExternalServiceCategory.METHOD_NOT_ALLOWED),
        (422, ExternalServiceCategory.INVALID_REQUEST),
        (423, ExternalServiceCategory.LOCKED),
        (424, ExternalServiceCategory.FAILED_DEPENDENCY),
        (501, ExternalServiceCategory.UNAVAILABLE),
    ],
)
def test_non_transient_http_response_does_not_retry(
    client: ParatranzClient, status: int, category: ExternalServiceCategory
) -> None:
    response = requests.Response()
    response.status_code = status
    response._content = b'{"message":"request rejected"}'
    response.raw = MagicMock()
    client._session.request = MagicMock(return_value=response)

    with pytest.raises(ExternalServiceError) as captured:
        client._request("PUT", "/projects/7/files/8")

    assert captured.value.category is category
    assert captured.value.status == status
    assert dict(captured.value.safe_context)["response"] == "request rejected"
    client._session.request.assert_called_once()
    response.raw.release_conn.assert_called_once()


def test_system_error_prefers_body_id_and_preserves_remote_code(client: ParatranzClient) -> None:
    response = requests.Response()
    response.status_code = 500
    response.headers["X-Request-ID"] = "header-correlation-1"
    response._content = b'{"message":"server error","code":"ER_DUP_ENTRY","id":"8f3a1b2c"}'
    client._session.request = MagicMock(return_value=response)

    with pytest.raises(ExternalServiceError) as captured:
        client._request("GET", "/projects", retryable=False)

    assert captured.value.request_id == "8f3a1b2c"
    assert captured.value.remote_code == "ER_DUP_ENTRY"
    assert "8f3a1b2c" in str(captured.value)


def test_request_local_credentials_are_redacted_from_error_and_transport_logs(client: ParatranzClient) -> None:
    key = "provider key+/some"
    secret = 'provider "secret"+/other'
    encoded_key = quote(key, safe="")
    encoded_secret = quote_plus(secret)
    response = requests.Response()
    response.status_code = 424
    response._content = json.dumps({
        "id": encoded_key,
        "code": secret,
        "message": f"provider rejected {key} and {encoded_secret}",
    }).encode()
    client._session.request = MagicMock(return_value=response)

    with pytest.raises(ExternalServiceError) as captured:
        client._request("GET", "/suggestions/mt", sensitive_values=(key, secret))

    assert captured.value.request_id == "***REDACTED***"
    assert captured.value.remote_code == "***REDACTED***"
    assert dict(captured.value.safe_context)["response"] == "provider rejected ***REDACTED*** and ***REDACTED***"

    log_messages = []

    def transport_failure(**kwargs):
        message = f"provider call {encoded_key}/{encoded_secret} failed"
        log_messages.append(DiagnosticLogFormatter().format(logging.makeLogRecord({"msg": message})))
        raise requests.ConnectionError(message)

    client._session.request.side_effect = transport_failure
    with pytest.raises(ExternalServiceError) as captured:
        client._request("GET", "/suggestions/mt", retryable=False, sensitive_values=(key, secret))

    assert captured.value.category is ExternalServiceCategory.TRANSPORT
    assert captured.value.__context__ is None
    assert log_messages == ["provider call ***REDACTED***/***REDACTED*** failed"]
    assert all(value not in str(captured.value) for value in (key, secret, encoded_key, encoded_secret))
