"""Single recursive redactor for tool, observation and storage boundaries."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
import re
from typing import Any, ClassVar

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{20,}", re.IGNORECASE),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?:ghp|gho|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"(?:xoxb|xoxp|xoxt)-[0-9]+-[0-9]+-[A-Za-z0-9]+"),
    re.compile(
        r"(?:password|passwd|secret|token|api_key|apikey)\s*[:=]\s*[\"']?[^\s\"'},]{8,}[\"']?",
        re.IGNORECASE,
    ),
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
})


class SecretRedactor:
    """Recursively redacts secrets without mutating the source value."""

    REDACTED = "***REDACTED***"
    _default: ClassVar[SecretRedactor | None] = None

    def __init__(self, *, redact_paths: bool = False) -> None:
        self._patterns = _SECRET_PATTERNS + (_FILE_PATH_PATTERNS if redact_paths else ())

    @classmethod
    def default(cls) -> SecretRedactor:
        if cls._default is None:
            cls._default = cls()
        return cls._default

    def redact_text(self, text: str) -> str:
        for pattern in self._patterns:
            text = pattern.sub(self.REDACTED, text)
        return text

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            redacted: dict[Any, Any] = {}
            for key, item in value.items():
                safe_key = self.redact_text(key) if isinstance(key, str) else key
                if isinstance(key, str) and key.casefold() in _SENSITIVE_KEYS:
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
                    self.REDACTED
                    if field.name.casefold() in _SENSITIVE_KEYS
                    else self.redact(getattr(value, field.name))
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
