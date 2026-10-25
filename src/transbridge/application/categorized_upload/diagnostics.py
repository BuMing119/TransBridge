"""Correlated diagnostics for classification, comparison and publication."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
from time import monotonic
from uuid import uuid4

from transbridge.application.security.diagnostic_logging import (
    DiagnosticLogFormatter,
    diagnostic_credentials,
    diagnostic_exception_text,
)
from transbridge.application.security.redaction import SecretRedactor

_logger = logging.getLogger(__name__)
_scope: ContextVar[dict] = ContextVar("categorized_upload_diagnostics", default={})


@contextmanager
def diagnostic_scope(phase: str, *, credential: str = "", **fields):
    parent = _scope.get()
    token = _scope.set({"operation_id": parent.get("operation_id") or uuid4().hex, **parent, **fields, "phase": phase})
    started = monotonic()
    try:
        with diagnostic_credentials(credential):
            log_event("phase_started")
            try:
                yield
            except Exception as exc:
                log_failure(phase, exc)
                raise
            else:
                log_event("phase_finished", elapsed_ms=round((monotonic() - started) * 1000))
    finally:
        _scope.reset(token)


def log_event(event: str, *, level: int = logging.INFO, **fields) -> None:
    if not _logger.isEnabledFor(level):
        return
    payload = SecretRedactor.default().redact({
        "event": "PARATRANZ_CATEGORIZED_UPLOAD",
        **_scope.get(),
        "step": event,
        **fields,
    })
    message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    # Sanitize before handing the record to any handler, including test capture.
    record = logging.makeLogRecord({"msg": message, "args": ()})
    safe = DiagnosticLogFormatter("%(message)s").format(record)
    _logger.log(
        level, "%s", safe.replace("\u0085", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )


def log_failure(action: str, exc: Exception, **fields) -> None:
    category = getattr(getattr(exc, "category", None), "value", None)
    response = dict(getattr(exc, "safe_context", ())).get("response", "")
    # A typed transport error may embed a server body in both str(exc) and
    # its traceback. HTTP diagnostics separately project its diagnostic fields.
    projections = (
        (response, repr(response), json.dumps(response, ensure_ascii=False)[1:-1], json.dumps(response)[1:-1])
        if response
        else ()
    )
    redactor = SecretRedactor(known_secrets=projections)
    log_event(
        "operation_failed",
        level=logging.WARNING if category == "cancelled" else logging.ERROR,
        action=action,
        status="cancelled" if category == "cancelled" else "failed",
        error_type=type(exc).__name__,
        reason=redactor.redact_text(diagnostic_exception_text(exc)),
        traceback=redactor.redact_text(DiagnosticLogFormatter().formatException((type(exc), exc, exc.__traceback__))),
        error_category=category,
        http_status=getattr(exc, "status", None),
        request_id=getattr(exc, "request_id", None),
        **fields,
    )
