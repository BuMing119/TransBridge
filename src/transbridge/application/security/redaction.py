"""Technical diagnostic redaction; business payloads use project_business_data."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass, replace
import re
from typing import Any, ClassVar

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[^\s,;\]}]+", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?:xoxb|xoxp|xoxt)-[0-9]+-[0-9]+-[A-Za-z0-9]+"),
    re.compile(
        r"(?:password|passwd|secret|token|api[-_ ]?key|authorization|credential)"
        r"[\"']?\s*[:=]\s*[\"']?[^\s\"'},]+[\"']?",
        re.IGNORECASE,
    ),
    re.compile(r"(?<=://)[^\s/@:]+:[^\s/@]+@"),
)
_FILE_PATH_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/][^\s\[\]\(\){}<>:;\"']+"),
    re.compile(r"/(?:home|etc|opt|var|tmp|usr|root|Users|Applications|Library)(?:/[^\s\[\]\(\){}<>:;\"']+)+"),
)
_SENSITIVE_KEYS = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "passwd",
    "password",
    "secret",
    "token",
    "credential",
    "credentials",
})


class SecretRedactor:
    """Protect credential-bearing diagnostics without mutating the source value."""

    REDACTED = "***REDACTED***"
    _default: ClassVar[SecretRedactor | None] = None

    def __init__(self, *, redact_paths: bool = False, known_secrets: Iterable[str] = ()) -> None:
        self._patterns = _SECRET_PATTERNS + (_FILE_PATH_PATTERNS if redact_paths else ())
        self._known_secrets = tuple(sorted({value for value in known_secrets if value}, key=len, reverse=True))

    @classmethod
    def default(cls) -> SecretRedactor:
        if cls._default is None:
            cls._default = cls()
        return cls._default

    def redact_text(self, text: str) -> str:
        for value in self._known_secrets:
            text = text.replace(value, self.REDACTED)
        for pattern in self._patterns:
            text = pattern.sub(self.REDACTED, text)
        return text

    def redact(self, value: Any) -> Any:
        from .data_projection import SecretValue

        if isinstance(value, SecretValue):
            return self.REDACTED
        if isinstance(value, BaseException):
            from .diagnostic_logging import diagnostic_exception_text

            return self.redact_text(diagnostic_exception_text(value))
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                safe_key = self.redact_text(key) if isinstance(key, str) else key
                if isinstance(key, str) and _is_sensitive_key(key):
                    redacted[safe_key] = self.REDACTED
                else:
                    redacted[safe_key] = self.redact(item)
            return redacted
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact(item) for item in value)
        if is_dataclass(value) and not isinstance(value, type):
            changes = {
                field.name: (
                    self.REDACTED if _is_sensitive_key(field.name) else self.redact(getattr(value, field.name))
                )
                for field in fields(value)
            }
            try:
                return replace(value, **changes)
            except TypeError:
                # Some dataclasses have positional-only or custom constructors.
                # Returning their already-redacted field projection is fail-closed;
                # returning the original object would leak through ``default=str``.
                return {"__type__": type(value).__name__, **changes}
        return value


def _is_sensitive_key(name: str) -> bool:
    normalized = name.casefold().replace("-", "_").replace(" ", "_")
    return normalized in _SENSITIVE_KEYS or any(normalized.endswith(f"_{key}") for key in _SENSITIVE_KEYS)
