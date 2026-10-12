"""Credential-scoped protection of complete technical log messages and tracebacks."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
import copy
from functools import wraps
import logging
import traceback

from .redaction import SecretRedactor

_request_secrets: ContextVar[tuple[str, ...]] = ContextVar("diagnostic_request_secrets", default=())
_SAFE_MESSAGE = "_transbridge_safe_diagnostic_message"
_SAFE_TRACEBACK = "_transbridge_safe_diagnostic_traceback"


def diagnostic_exception_text(exc: BaseException) -> str:
    """Project an exception after its request ends, retaining source-bound protection."""
    cached = getattr(exc, _SAFE_MESSAGE, None)
    return cached if isinstance(cached, str) else SecretRedactor.default().redact_text(str(exc))


def client_diagnostic_redactor(client: object) -> SecretRedactor:
    """Use only this client's existing credential, without a global plaintext registry."""
    return SecretRedactor(known_secrets=_client_secrets(client))


def _client_secrets(client: object) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[int] = set()
    while client is not None and id(client) not in seen:
        seen.add(id(client))
        state = vars(client) if hasattr(client, "__dict__") else {}
        credential = state.get("_api_key")
        if isinstance(credential, str) and credential:
            values.append(credential)
        client = state.get("_delegate")
    return tuple(values)


@contextmanager
def diagnostic_credentials(*values: str):
    """Limit known credential material to the active request's diagnostic context."""
    token = _request_secrets.set((*_request_secrets.get(), *(value for value in values if value)))
    try:
        yield
    finally:
        _request_secrets.reset(token)


def credential_diagnostics(method):
    """Bind provider logs to that client for one synchronous request."""

    @wraps(method)
    def call(self, *args, **kwargs):
        with diagnostic_credentials(*_client_secrets(self)):
            try:
                return method(self, *args, **kwargs)
            except Exception as exc:
                redactor = SecretRedactor(known_secrets=_request_secrets.get())
                # Cache only safe projections, never a credential or an SDK payload.
                # Keep the original exception type, arguments and status for retry logic.
                setattr(exc, _SAFE_MESSAGE, redactor.redact_text(str(exc)))
                setattr(exc, _SAFE_TRACEBACK, redactor.redact_text("".join(traceback.format_exception(exc))))
                raise

    return call


class DiagnosticLogFormatter(logging.Formatter):
    """Protect the final formatted record, including exception chains and stack_info."""

    def format(self, record: logging.LogRecord) -> str:
        detached = copy.copy(record)
        detached.msg = _project_exception_arguments(record.msg)
        detached.args = _project_exception_arguments(record.args)
        detached.exc_text = None
        text = super().format(detached)
        return SecretRedactor(known_secrets=_request_secrets.get()).redact_text(text)

    def formatException(self, ei) -> str:
        cached = getattr(ei[1], _SAFE_TRACEBACK, None)
        return cached.rstrip() if isinstance(cached, str) else super().formatException(ei)


def _project_exception_arguments(value):
    if isinstance(value, BaseException):
        return diagnostic_exception_text(value)
    if isinstance(value, Mapping):
        return {key: _project_exception_arguments(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_project_exception_arguments(item) for item in value)
    if isinstance(value, list):
        return [_project_exception_arguments(item) for item in value]
    return value
