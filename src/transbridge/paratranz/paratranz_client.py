"""HTTP adapter with typed errors, bounded retry and cooperative cancellation."""

from __future__ import annotations

from dataclasses import dataclass
import ssl
import time
from typing import Any
import uuid

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from transbridge.application.ports.paratranz import (
    CancellationPort,
    ExternalServiceCategory,
    ExternalServiceError,
)
from transbridge.config.paratranz_credentials import (
    UnavailableCredentialStore,
    redact_credential_data,
)

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

    def _safe(self, value: Any) -> Any:
        return redact_credential_data(value, self.config._secret)

    def _error(
        self,
        category: ExternalServiceCategory,
        message: str,
        *,
        endpoint: str,
        status: int | None = None,
        request_id: str | None = None,
        retry_after: float | None = None,
        response_text: str | None = None,
    ) -> ExternalServiceError:
        context = {"endpoint": str(self._safe(endpoint))}
        if response_text:
            context["response"] = str(self._safe(response_text))[:512]
        safe_request_id = str(self._safe(request_id))[:128] if request_id else None
        return ExternalServiceError(
            category,
            message,
            status=status,
            request_id=safe_request_id,
            retry_after=retry_after,
            safe_context=context,
        )

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(float(raw), 0.0)
        except ValueError:
            return None

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
        expected_type: type | tuple[type, ...] | None = None,
        raw_response: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Send one typed request with safe retry and cancellation checkpoints."""

        normalized_method = method.upper()
        retryable_operation = (
            normalized_method in self._IDEMPOTENT_METHODS or bool(idempotency_key) or confirmed_idempotent
        )
        url = f"{self.config.base_url}{endpoint}"
        headers = self._request_headers()
        supplied_headers = kwargs.pop("headers", {})
        headers.update((key, value) for key, value in supplied_headers.items() if key.casefold() != "authorization")
        request_id = uuid.uuid4().hex
        headers["X-Request-ID"] = request_id
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            try:
                response = self._session.request(
                    method=normalized_method,
                    url=url,
                    headers=headers,
                    timeout=self.config.timeout,
                    **kwargs,
                )
            except requests.Timeout as exc:
                error = self._error(
                    ExternalServiceCategory.TIMEOUT,
                    "ParaTranz request timed out",
                    endpoint=endpoint,
                    request_id=request_id,
                    response_text=str(exc),
                )
                if not retryable_operation or attempt == self.retry_policy.max_attempts:
                    raise error from None
                self._wait(self.retry_policy.delay(attempt), cancellation)
                continue
            except requests.RequestException as exc:
                error = self._error(
                    ExternalServiceCategory.TRANSPORT,
                    "ParaTranz transport failed",
                    endpoint=endpoint,
                    request_id=request_id,
                    response_text=str(exc),
                )
                if not retryable_operation or attempt == self.retry_policy.max_attempts:
                    raise error from None
                self._wait(self.retry_policy.delay(attempt), cancellation)
                continue

            if cancellation is not None:
                cancellation.raise_if_cancelled()
            response_request_id = (
                response.headers.get("X-Request-ID") or response.headers.get("X-Correlation-ID") or request_id
            )
            retry_after = self._retry_after(response)
            category = self._status_category(response.status_code)
            if category is not None:
                error = self._error(
                    category,
                    "ParaTranz request failed",
                    endpoint=endpoint,
                    status=response.status_code,
                    request_id=response_request_id,
                    retry_after=retry_after,
                    response_text=response.text,
                )
                can_retry = category in {
                    ExternalServiceCategory.RATE_LIMITED,
                    ExternalServiceCategory.UNAVAILABLE,
                }
                if not retryable_operation or not can_retry or attempt == self.retry_policy.max_attempts:
                    raise error
                self._wait(self.retry_policy.delay(attempt, retry_after), cancellation)
                continue

            if raw_response:
                return response
            if response.status_code == 204 or not response.content.strip():
                return None
            try:
                result = response.json()
            except ValueError:
                raise self._error(
                    ExternalServiceCategory.INVALID_RESPONSE,
                    "ParaTranz returned malformed JSON",
                    endpoint=endpoint,
                    status=response.status_code,
                    request_id=response_request_id,
                ) from None
            if expected_type is not None and not isinstance(result, expected_type):
                raise self._error(
                    ExternalServiceCategory.INVALID_RESPONSE,
                    "ParaTranz response schema is invalid",
                    endpoint=endpoint,
                    status=response.status_code,
                    request_id=response_request_id,
                )
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            return result
        raise RuntimeError("unreachable ParaTranz retry state")

    @staticmethod
    def _status_category(status: int) -> ExternalServiceCategory | None:
        if 200 <= status < 300:
            return None
        return {
            401: ExternalServiceCategory.AUTHENTICATION,
            403: ExternalServiceCategory.AUTHORIZATION,
            404: ExternalServiceCategory.NOT_FOUND,
            409: ExternalServiceCategory.CONFLICT,
            429: ExternalServiceCategory.RATE_LIMITED,
        }.get(status, ExternalServiceCategory.UNAVAILABLE)

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
