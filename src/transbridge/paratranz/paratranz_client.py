# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""HTTP adapter with typed errors, bounded retry and cooperative cancellation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from math import isfinite
import ssl
import time
from typing import Any
from urllib.parse import quote, quote_plus
import uuid

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceCategory,
    ExternalServiceError,
)
from transbridge.application.security.diagnostic_logging import diagnostic_credentials
from transbridge.application.security.redaction import SecretRedactor
from transbridge.config.paratranz_credentials import UnavailableCredentialStore

from .config_manager import ParatranzConfig


class ParatranzCredentialRequiredError(ExternalServiceError):
    """Stable prerequisite failure for entrypoints without a credential."""

    error_code = "PARATRANZ_CREDENTIAL_REQUIRED"

    def __init__(self) -> None:
        super().__init__(ExternalServiceCategory.AUTHENTICATION, "ParaTranz credential is required")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    initial_backoff: float = 0.25
    maximum_backoff: float = 5.0

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if self.initial_backoff < 0 or self.maximum_backoff < 0:
            raise ValueError("retry backoff must not be negative")
        if self.initial_backoff > self.maximum_backoff:
            raise ValueError("initial_backoff must not exceed maximum_backoff")

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.maximum_backoff)
        return min(self.initial_backoff * (2 ** max(attempt - 1, 0)), self.maximum_backoff)


class _SSLAdapter(HTTPAdapter):
    """Tolerate the known ParaTranz TLS close-notify omission."""

    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context()
        context.options |= ssl.OP_IGNORE_UNEXPECTED_EOF
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


class ParatranzClient:
    """Compatibility HTTP adapter implementing the typed remote boundary."""

    _IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
    _RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        token: str | ParatranzConfig | None = None,
        timeout: int = 10,
        config: ParatranzConfig | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        session: requests.Session | None = None,
    ) -> None:
        if isinstance(token, ParatranzConfig) and config is None:
            config = token
            token = None
        elif token is not None and not isinstance(token, str) and config is None:
            config, token = self._normalize_legacy_config(token, timeout), None
        if config is None:
            self.config = ParatranzConfig(token=token, timeout=timeout)
        else:
            self.config = config
            if token:
                self.config.update_token(token)
            if timeout != 10:
                self.config.update_timeout(timeout)
        self.retry_policy = retry_policy or RetryPolicy()
        self._session = session or requests.Session()
        self._session.mount("https://", _SSLAdapter())

    @staticmethod
    def _normalize_legacy_config(legacy: object, timeout: int) -> ParatranzConfig:
        candidate = getattr(legacy, "token", None)
        if not isinstance(candidate, str) or not candidate.strip():
            candidate = getattr(legacy, "api_token", None)
        token = candidate.strip() if isinstance(candidate, str) and candidate.strip() else None
        base_url = getattr(legacy, "base_url", None)
        legacy_timeout = getattr(legacy, "timeout", None)
        return ParatranzConfig(
            token=token,
            base_url=(base_url if isinstance(base_url, str) and base_url else ParatranzConfig.DEFAULT_BASE_URL),
            timeout=(
                legacy_timeout if isinstance(legacy_timeout, int) and not isinstance(legacy_timeout, bool) else timeout
            ),
            credential_store=UnavailableCredentialStore(),
        )

    def close(self) -> None:
        self._session.close()

    def _request_headers(self) -> dict[str, str]:
        headers = self.config.get_headers()
        if not headers.get("Authorization"):
            raise ParatranzCredentialRequiredError()
        return headers

    @staticmethod
    def _error(
        category: ExternalServiceCategory,
        message: str,
        *,
        endpoint: str,
        redactor: SecretRedactor,
        status: int | None = None,
        request_id: str | None = None,
        remote_code: str | None = None,
        retry_after: float | None = None,
        response_text: str | None = None,
    ) -> ExternalServiceError:
        context = {"endpoint": redactor.redact_text(endpoint)}
        if response_text:
            context["response"] = redactor.redact_text(response_text)[:512]
        return ExternalServiceError(
            category,
            message,
            status=status,
            request_id=" ".join(redactor.redact_text(request_id).splitlines())[:128] if request_id else None,
            remote_code=" ".join(redactor.redact_text(remote_code).splitlines())[:128] if remote_code else None,
            retry_after=retry_after,
            safe_context=context,
        )

    def _status_error(
        self,
        response: requests.Response,
        category: ExternalServiceCategory,
        *,
        endpoint: str,
        request_id: str,
        redactor: SecretRedactor,
    ) -> ExternalServiceError:
        body = {}
        try:
            response_text = response.text
        except requests.RequestException as exc:
            response_text = str(exc)
        else:
            try:
                decoded = json.loads(response_text)
            except ValueError:
                decoded = None
            if isinstance(decoded, dict):
                body = decoded
        remote_id, code, message = (body.get(key) for key in ("id", "code", "message"))
        return self._error(
            category,
            "ParaTranz request failed",
            endpoint=endpoint,
            redactor=redactor,
            status=response.status_code,
            request_id=remote_id if isinstance(remote_id, str) and remote_id.strip() else request_id,
            remote_code=str(code) if type(code) in (str, int) else None,
            retry_after=self._retry_after(response),
            response_text=message if isinstance(message, str) and message.strip() else response_text,
        )

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            seconds = float(raw)
        except ValueError:
            return None
        return max(seconds, 0.0) if isfinite(seconds) else None

    def _wait(self, delay: float, cancellation: CancellationPort | None) -> None:
        if cancellation is None:
            time.sleep(delay)
            return
        if cancellation.wait(delay):
            cancellation.raise_if_cancelled()
        cancellation.raise_if_cancelled()

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        cancellation: CancellationPort | None = None,
        idempotency_key: str | None = None,
        confirmed_idempotent: bool = False,
        retryable: bool | None = None,
        expected_type: type | tuple[type, ...] | None = None,
        raw_response: bool = False,
        sensitive_values: tuple[str, ...] = (),
        **kwargs: Any,
    ) -> Any:
        """Send one typed request, protecting any per-call secrets in diagnostics."""

        normalized_method = method.upper()
        retryable_operation = (
            normalized_method in self._IDEMPOTENT_METHODS or bool(idempotency_key) or confirmed_idempotent
        )
        if retryable is not None:
            retryable_operation = retryable
        url = f"{self.config.base_url}{endpoint}"
        headers = self._request_headers()
        supplied_headers = kwargs.pop("headers", {})
        headers.update((key, value) for key, value in supplied_headers.items() if key.casefold() != "authorization")
        request_id = uuid.uuid4().hex
        headers["X-Request-ID"] = request_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        diagnostic_values = tuple(
            representation
            for value in (headers["Authorization"].removeprefix("Bearer "), *sensitive_values)
            if value
            for representation in (
                value,
                quote(value, safe=""),
                quote_plus(value),
                json.dumps(value, ensure_ascii=False)[1:-1],
                json.dumps(value)[1:-1],
            )
        )
        redactor = SecretRedactor(known_secrets=diagnostic_values)

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            try:
                with diagnostic_credentials(*diagnostic_values):
                    response = self._session.request(
                        method=normalized_method,
                        url=url,
                        headers=headers,
                        timeout=self.config.timeout,
                        **kwargs,
                    )
            except requests.RequestException as exc:
                timed_out = isinstance(exc, requests.Timeout)
                error = self._error(
                    ExternalServiceCategory.TIMEOUT if timed_out else ExternalServiceCategory.TRANSPORT,
                    "ParaTranz request timed out" if timed_out else "ParaTranz transport failed",
                    endpoint=endpoint,
                    redactor=redactor,
                    request_id=request_id,
                    response_text=str(exc),
                )
                can_retry = True
            else:
                handed_off = False
                try:
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    response_request_id = response.headers.get("X-Request-ID") or response.headers.get(
                        "X-Correlation-ID"
                    )
                    if not isinstance(response_request_id, str) or not response_request_id.strip():
                        response_request_id = request_id
                    category = self._status_category(response.status_code)
                    if category is None:
                        if raw_response:
                            handed_off = True
                            return response
                        return self._decode_response(
                            response,
                            endpoint=endpoint,
                            request_id=response_request_id,
                            redactor=redactor,
                            expected_type=expected_type,
                            cancellation=cancellation,
                        )
                    error = self._status_error(
                        response, category, endpoint=endpoint, request_id=response_request_id, redactor=redactor
                    )
                    can_retry = response.status_code in self._RETRYABLE_STATUSES
                finally:
                    if not handed_off and response.raw is not None:
                        response.close()

            if not retryable_operation or not can_retry or attempt == self.retry_policy.max_attempts:
                raise error from None
            self._wait(self.retry_policy.delay(attempt, error.retry_after), cancellation)
        raise RuntimeError("unreachable ParaTranz retry state")

    def _decode_response(
        self,
        response: requests.Response,
        *,
        endpoint: str,
        request_id: str,
        redactor: SecretRedactor,
        expected_type: type | tuple[type, ...] | None,
        cancellation: CancellationPort | None,
    ) -> Any:
        if response.status_code == 204 or not response.content.strip():
            return None
        result = None
        message = None
        try:
            result = response.json()
        except ValueError:
            message = "ParaTranz returned malformed JSON"
        else:
            if expected_type is not None and not isinstance(result, expected_type):
                message = "ParaTranz response schema is invalid"
        if message:
            raise self._error(
                ExternalServiceCategory.INVALID_RESPONSE,
                message,
                endpoint=endpoint,
                redactor=redactor,
                status=response.status_code,
                request_id=request_id,
            ) from None
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        return result

    @staticmethod
    def _status_category(status: int) -> ExternalServiceCategory | None:
        if 200 <= status < 300:
            return None
        if 500 <= status < 600:
            return ExternalServiceCategory.UNAVAILABLE
        if not 400 <= status < 500:
            return ExternalServiceCategory.INVALID_RESPONSE
        return {
            401: ExternalServiceCategory.AUTHENTICATION,
            403: ExternalServiceCategory.AUTHORIZATION,
            404: ExternalServiceCategory.NOT_FOUND,
            405: ExternalServiceCategory.METHOD_NOT_ALLOWED,
            409: ExternalServiceCategory.CONFLICT,
            423: ExternalServiceCategory.LOCKED,
            424: ExternalServiceCategory.FAILED_DEPENDENCY,
            429: ExternalServiceCategory.RATE_LIMITED,
        }.get(status, ExternalServiceCategory.INVALID_REQUEST)

    def _request_multipart(
        self,
        method: str,
        endpoint: str,
        body: bytes,
        content_type: str,
        *,
        cancellation: CancellationPort | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        return self._request(
            method,
            endpoint,
            cancellation=cancellation,
            idempotency_key=idempotency_key,
            headers={"Content-Type": content_type},
            data=body,
        )
