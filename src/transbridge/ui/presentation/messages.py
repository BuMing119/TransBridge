"""User-facing message values without widget or localization ownership."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MessageSeverity(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class UiMessage:
    """A presentation-safe message; raw exceptions stay at the boundary."""

    code: str
    text: str
    severity: MessageSeverity = MessageSeverity.INFO
    retryable: bool = False

    def __post_init__(self) -> None:
        if not self.code.strip():
            raise ValueError("message code is required")
        if not self.text.strip():
            raise ValueError("message text is required")
