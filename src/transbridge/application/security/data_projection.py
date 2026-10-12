"""Preserve business payloads while explicitly protecting diagnostic and secret values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any

from .redaction import SecretRedactor


class SecretValue:
    """Opaque credential; ordinary string projections never reveal its value."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        self.__value = value

    def __repr__(self) -> str:
        return "SecretValue(***)"

    def __str__(self) -> str:
        return SecretRedactor.REDACTED

    def _reveal_for_request(self) -> str:
        """Reveal only at a credential-bound request or secure-storage boundary."""
        return self.__value


@dataclass(frozen=True, slots=True)
class DiagnosticValue:
    """Mark a technical diagnostic whose strings may contain credential material."""

    value: Any


def project_business_data(value: Any) -> Any:
    """Detach payload containers, preserving business strings and identifiers exactly."""
    if isinstance(value, SecretValue):
        return SecretRedactor.REDACTED
    if isinstance(value, DiagnosticValue):
        return SecretRedactor.default().redact(value.value)
    if isinstance(value, Mapping):
        return {project_business_data(key): project_business_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [project_business_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(project_business_data(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        changes = {field.name: project_business_data(getattr(value, field.name)) for field in fields(value)}
        try:
            return replace(value, **changes)
        except TypeError:
            return {"__type__": type(value).__name__, **changes}
    return value
