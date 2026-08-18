"""Stable application error categories and exception mapping."""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
from enum import StrEnum
from typing import Any


class ErrorCategory(StrEnum):
    """Categories that entrypoint adapters may rely on."""

    INPUT = "input"
    PREREQUISITE = "prerequisite"
    PERMISSION = "permission"
    CONFLICT = "conflict"
    EXTERNAL = "external"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


class DomainError(Exception):
    """A classified error safe to translate into an application diagnostic.

    ``message`` is safe for callers. The original exception is retained as the
    Python cause for trusted logs, but is never included in serialized output.
    """

    def __init__(
        self,
        category: ErrorCategory,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ) -> None:
        if not code or not code.strip():
            raise ValueError("DomainError code must not be empty")
        if not message or not message.strip():
            raise ValueError("DomainError message must not be empty")
        super().__init__(message)
        self.category = category
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})
        if cause is not None:
            self.__cause__ = cause

    def to_dict(self) -> dict[str, Any]:
        """Return caller-safe fields; the exception cause is deliberately absent."""

        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


def map_exception(exc: BaseException) -> DomainError:
    """Map an arbitrary exception without ever turning it into success.

    Known application errors retain their classification. Unexpected errors use
    a deliberately generic public message so paths, tokens, and other secrets in
    ``str(exc)`` cannot cross the application boundary.
    """

    if isinstance(exc, DomainError):
        return exc
    if isinstance(exc, (asyncio.CancelledError, FutureCancelledError)):
        return DomainError(
            ErrorCategory.CANCELLED,
            "OPERATION_CANCELLED",
            "The operation was cancelled.",
            cause=exc,
        )
    if isinstance(exc, PermissionError):
        return DomainError(
            ErrorCategory.PERMISSION,
            "PERMISSION_DENIED",
            "Permission was denied.",
            cause=exc,
        )
    if isinstance(exc, FileNotFoundError):
        return DomainError(
            ErrorCategory.PREREQUISITE,
            "RESOURCE_NOT_FOUND",
            "A required resource is unavailable.",
            cause=exc,
        )
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return DomainError(
            ErrorCategory.EXTERNAL,
            "EXTERNAL_SERVICE_ERROR",
            "An external service is unavailable.",
            retryable=True,
            cause=exc,
        )
    if isinstance(exc, (ValueError, TypeError)):
        return DomainError(
            ErrorCategory.INPUT,
            "INVALID_INPUT",
            "The request is invalid.",
            cause=exc,
        )
    return DomainError(
        ErrorCategory.INTERNAL,
        "INTERNAL_ERROR",
        "An internal error occurred.",
        cause=exc,
    )
