from __future__ import annotations

from dataclasses import dataclass
import logging
import sys

from transbridge.application.security import (
    DiagnosticValue,
    SecretRedactor,
    SecretValue,
    diagnostic_exception_text,
    project_business_data,
)
from transbridge.application.security.diagnostic_logging import (
    DiagnosticLogFormatter,
    client_diagnostic_redactor,
    credential_diagnostics,
    diagnostic_credentials,
)
from transbridge.config.paratranz_credentials import SecretValue as LegacySecretValue, redact_credential_data


def test_business_projection_preserves_identifiers_fields_and_text_but_detaches():
    original = "password: swordfish; token=questreward; sk-abcdefghijk; C:\\mods\\quest.esp"
    key = f'["plugin", "quest", "{original}"]'
    payload = {key: {"original": original, "password": original, "token": [original]}}
    result = project_business_data(payload)
    assert result == payload
    assert result is not payload
    assert result[key] is not payload[key]
    assert result[key]["token"] is not payload[key]["token"]


def test_typed_secret_and_diagnostics_are_protected_without_guessing_business_fields():
    @dataclass(frozen=True)
    class Payload:
        original: str
        credential: SecretValue
        diagnostic: DiagnosticValue

    source = Payload("password: swordfish", SecretValue("actual-credential"), DiagnosticValue("api_key=real-value"))
    projected = project_business_data(source)
    assert projected.original == source.original
    assert projected.credential == SecretRedactor.REDACTED
    assert "real-value" not in projected.diagnostic
    assert isinstance(source.credential, SecretValue)
    assert LegacySecretValue is SecretValue
    assert project_business_data(DiagnosticValue(SecretValue("actual-credential"))) == SecretRedactor.REDACTED
    assert project_business_data(DiagnosticValue({"nested": [SecretValue("actual-credential")]})) == {
        "nested": [SecretRedactor.REDACTED]
    }


def test_technical_redactor_accepts_known_credential_and_sensitive_headers():
    redactor = SecretRedactor(known_secrets=("opaque-canary",))
    source = {
        "error": "provider rejected opaque-canary",
        "headers": {"X-Api-Key": "short", "Authorization": "opaque-canary"},
        "url": "https://name:private@example.test/api?token=abc",
    }
    result = str(redactor.redact(source))
    for value in ("opaque-canary", "short", "private", "token=abc"):
        assert value not in result


def test_explicit_diagnostic_exception_is_projected_before_serialization():
    exception = RuntimeError("api_key=actual-provider-key")
    projected = project_business_data({"error": DiagnosticValue(exception)})
    assert isinstance(projected["error"], str)
    assert "actual-provider-key" not in projected["error"]
    assert str(exception) == "api_key=actual-provider-key"


def test_paratranz_known_credential_projection_covers_dataclass_diagnostics():
    @dataclass(frozen=True)
    class ErrorPayload:
        message: str

    value = ErrorPayload("service rejected opaque-canary")
    projected = redact_credential_data(value, SecretValue("opaque-canary"))
    assert isinstance(projected, ErrorPayload)
    assert "opaque-canary" not in projected.message
    assert value.message == "service rejected opaque-canary"


def test_formatter_covers_exception_chain_and_resets_request_known_values():
    formatter = DiagnosticLogFormatter("%(message)s")
    try:
        try:
            raise ValueError("opaque-canary")
        except ValueError as exc:
            raise RuntimeError("api_key=outer-secret") from exc
    except RuntimeError:
        record = logging.LogRecord("boundary", logging.ERROR, __file__, 1, "request failed", (), sys.exc_info())
    with diagnostic_credentials("opaque-canary"):
        formatted = formatter.format(record)
    assert "opaque-canary" not in formatted and "outer-secret" not in formatted
    assert "ValueError" in formatted and "RuntimeError" in formatted
    assert record.exc_text is None
    # No permanently registered plaintext credential remains after the call.
    ordinary = logging.LogRecord("boundary", logging.INFO, __file__, 1, "opaque-canary", (), None)
    assert formatter.format(ordinary) == "opaque-canary"


def test_client_credential_scope_protects_actual_logs_and_unwraps_delegate():
    rendered = []

    class Handler(logging.Handler):
        def emit(self, record):
            rendered.append(self.format(record))

    logger = logging.getLogger("test.boundary.client")
    handler = Handler()
    handler.setFormatter(DiagnosticLogFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    class Client:
        _api_key = "class-value-is-not-an-instance-credential"

        def __init__(self):
            self._api_key = "opaque-client-canary"

        @credential_diagnostics
        def chat(self):
            try:
                raise RuntimeError(self._api_key)
            except RuntimeError:
                logger.exception("provider failed")

    class Wrapper:
        def __init__(self, delegate):
            self._delegate = delegate

    client = Client()
    try:
        client.chat()
    finally:
        logger.removeHandler(handler)
    assert "opaque-client-canary" not in rendered[0]
    assert "opaque-client-canary" not in client_diagnostic_redactor(Wrapper(client)).redact_text(client._api_key)


def test_source_bound_diagnostic_survives_request_scope_without_changing_exception():
    class ProviderError(RuntimeError):
        status_code = 429

    class Client:
        def __init__(self):
            self._api_key = "opaque-source-canary"

        @credential_diagnostics
        def chat(self):
            raise ProviderError(f"provider rejected {self._api_key}")

    try:
        Client().chat()
    except ProviderError as exc:
        assert exc.status_code == 429
        assert exc.args == ("provider rejected opaque-source-canary",)
        assert diagnostic_exception_text(exc) == "provider rejected ***REDACTED***"
        record = logging.LogRecord(
            "after-call", logging.ERROR, __file__, 1, "request failed: %s", (exc,), sys.exc_info()
        )
        rendered = DiagnosticLogFormatter().format(record)
        assert "opaque-source-canary" not in rendered
        assert "ProviderError" in rendered
    else:
        raise AssertionError("original exception must propagate")
