"""Transparent LLM client wrapper that persists request and response details."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import logging
import re
import threading
from typing import Any

from transbridge.infra.llm_client import LLMClient

from .workflow_log_store import WorkflowLogStore

_logger = logging.getLogger(__name__)

_REDACTED = "***REDACTED***"
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[^\s,;\]}]+")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[-_ ]?key|authorization|credential|password|secret|token)(\s*[:=]\s*)([^\s,;\]}]+)"
)
_JSON_SECRET_PATTERN = re.compile(
    r"""(?i)(["'](?:api[-_ ]?key|authorization|credential|password|secret|token)["']\s*:\s*)(["'])(.*?)(\2)"""
)
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


class WorkflowLoggingLLMClient(LLMClient):
    """Delegate LLM calls unchanged while recording each call in its own log."""

    def __init__(
        self,
        delegate: LLMClient,
        store: WorkflowLogStore,
        *,
        channel_prefix: str = "llm_call",
        grouped: bool = False,
    ) -> None:
        self._delegate = delegate
        self._store = store
        self._channel_prefix = channel_prefix
        self._grouped = grouped
        self._counter = 0
        self._lock = threading.Lock()

    def chat(self, messages: list[dict], max_tokens: int = 0) -> str:
        _call_number, channel = self._reserve_call()
        self._write_request(channel, messages, max_tokens)
        try:
            response = self._delegate.chat(messages, max_tokens)
        except Exception as exc:
            self._write_error(channel, exc)
            raise
        self._write_success(channel, response)
        return response

    def chat_prepared(self, messages_factory: Callable[[], list[dict]], max_tokens: int = 0) -> str:
        """Reserve logging identity, then let an admission wrapper prepare the request."""

        _call_number, channel = self._reserve_call()

        def prepare_and_log() -> list[dict]:
            messages = messages_factory()
            self._write_request(channel, messages, max_tokens)
            return messages

        try:
            prepared_chat = getattr(self._delegate, "chat_prepared", None)
            if callable(prepared_chat):
                response = prepared_chat(prepare_and_log, max_tokens)
            else:
                response = self._delegate.chat(prepare_and_log(), max_tokens)
        except Exception as exc:
            self._write_error(channel, exc)
            raise
        self._write_success(channel, response)
        return response

    def chat_stream(self, messages: list[dict], max_tokens: int, chunk_callback) -> str:
        _call_number, channel = self._reserve_call()
        self._write_request(channel, messages, max_tokens)

        def record_chunk(chunk: str) -> None:
            self._safe_write_chunk(channel, _redact_text(chunk))
            chunk_callback(chunk)

        try:
            response = self._delegate.chat_stream(messages, max_tokens, record_chunk)
        except Exception as exc:
            self._write_error(channel, exc)
            raise
        self._write_success(channel, response, response_already_logged=True)
        return response

    def chat_stream_prepared(
        self,
        messages_factory: Callable[[], list[dict]],
        max_tokens: int,
        chunk_callback,
    ) -> str:
        """Streaming prepared-call path with admission-before-preparation support."""

        _call_number, channel = self._reserve_call()

        def prepare_and_log() -> list[dict]:
            messages = messages_factory()
            self._write_request(channel, messages, max_tokens)
            return messages

        def record_chunk(chunk: str) -> None:
            self._safe_write_chunk(channel, _redact_text(chunk))
            chunk_callback(chunk)

        try:
            prepared_stream = getattr(self._delegate, "chat_stream_prepared", None)
            if callable(prepared_stream):
                response = prepared_stream(prepare_and_log, max_tokens, record_chunk)
            else:
                response = self._delegate.chat_stream(prepare_and_log(), max_tokens, record_chunk)
        except Exception as exc:
            self._write_error(channel, exc)
            raise
        self._write_success(channel, response, response_already_logged=True)
        return response

    def cancel(self) -> None:
        self._delegate.cancel()

    def _reserve_call(self) -> tuple[int, str]:
        with self._lock:
            self._counter += 1
            call_number = self._counter
            channel = self._channel_prefix if self._grouped else f"{self._channel_prefix}_{call_number:03d}"
        self._safe_write_chunk(channel, f"[CALL {call_number:03d}]\n")
        return call_number, channel

    def _write_request(self, channel: str, messages: list[dict], max_tokens: int) -> None:
        try:
            request = json.dumps(_redact_value(messages), ensure_ascii=False, indent=2, default=str)
        except (TypeError, ValueError):
            request = _redact_text(repr(messages))
        self._safe_write_chunk(
            channel,
            f"[REQUEST TO LLM]\nmax_tokens={max_tokens}\n{request}\n\n[RESPONSE FROM LLM]\n",
        )

    def _write_success(self, channel: str, response: str, *, response_already_logged: bool = False) -> None:
        if not response_already_logged:
            self._safe_write_chunk(channel, f"{_redact_text(response)}\n")
        if not response or not response.strip():
            self._safe_write_line(channel, "[EMPTY RESPONSE] Provider returned an empty successful response")
        self._write_budget_metrics(channel)
        self._safe_write_chunk(channel, "\n[END CALL]\n\n")

    def _write_error(self, channel: str, exc: Exception) -> None:
        details = _exception_details(exc)
        payload = json.dumps(details, ensure_ascii=False, indent=2, default=str)
        self._safe_write_chunk(channel, f"\n[ERROR]\n{payload}\n")
        self._write_budget_metrics(channel)
        self._safe_write_chunk(channel, "[END CALL]\n\n")

    def _write_budget_metrics(self, channel: str) -> None:
        metrics = getattr(self._delegate, "last_call_metrics", None)
        if metrics is None:
            return
        self._safe_write_line(
            channel,
            "[REQUEST BUDGET] "
            f"wait_ms={metrics.admission_wait_ms} "
            f"in_flight={metrics.in_flight_at_admission} "
            f"peak_in_flight={metrics.peak_in_flight}",
        )

    def _safe_write_chunk(self, channel: str, content: str) -> None:
        try:
            self._store.write_chunk(channel, content)
        except Exception as exc:
            _logger.warning("记录 LLM 调用日志失败，模型调用继续: %s", exc)

    def _safe_write_line(self, channel: str, content: str) -> None:
        try:
            self._store.write_line(channel, content)
        except Exception as exc:
            _logger.warning("记录 LLM 调用错误日志失败，保留原始异常: %s", exc)


def _exception_details(exc: Exception) -> dict[str, Any]:
    details: dict[str, Any] = {
        "exception_type": type(exc).__name__,
        "message": _redact_text(str(exc)),
    }
    for field in ("status_code", "status", "code", "body", "error", "request_id", "requestId"):
        value = _safe_field(exc, field)
        if value is not None:
            details[field] = _redact_value(value)
    exception_request_ids = _request_id_headers(_safe_field(exc, "headers"))
    if exception_request_ids:
        details["request_ids"] = exception_request_ids

    response = _safe_field(exc, "response")
    if response is not None:
        response_details: dict[str, Any] = {}
        for field in ("status_code", "status", "code", "request_id", "requestId"):
            value = _safe_field(response, field)
            if value is not None:
                response_details[field] = _redact_value(value)
        body = _safe_field(response, "body")
        if body is None:
            body = _safe_field(response, "text")
        if body is None:
            response_json = _safe_field(response, "json")
            if callable(response_json):
                try:
                    body = response_json()
                except Exception:
                    body = None
        if body is not None:
            response_details["body"] = _redact_value(body)
        headers = _safe_field(response, "headers")
        request_ids = _request_id_headers(headers)
        if request_ids:
            response_details["request_ids"] = request_ids
        if response_details:
            details["response"] = response_details
    return details


def _safe_field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    try:
        return getattr(value, name, None)
    except Exception:
        return None


def _request_id_headers(headers: object) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, value in headers.items():
        normalized = str(key).casefold().replace("_", "-")
        if normalized in {"request-id", "x-request-id", "x-amzn-requestid", "cf-ray"}:
            result[str(key)] = _redact_text(str(value))
    return result


def _redact_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _REDACTED if _is_secret_key(str(key)) else _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def _is_secret_key(name: str) -> bool:
    normalized = name.casefold().replace("-", "_").replace(" ", "_")
    return normalized in _SECRET_KEYS or any(normalized.endswith(f"_{key}") for key in _SECRET_KEYS)


def _redact_text(value: str) -> str:
    value = _JSON_SECRET_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}{match.group(4)}", value
    )
    value = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", value)
    value = _SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}", value)
    return _OPENAI_KEY_PATTERN.sub(_REDACTED, value)


__all__ = ["WorkflowLoggingLLMClient"]
