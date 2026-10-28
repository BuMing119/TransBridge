"""Typed process messages that remain compatible with existing string log callbacks.

Callbacks used by CLI and extensions still receive a string. The task UI also
uses the kind, occurrence time and batch identity without parsing the wording.
Only user-facing summaries belong here; exception details stay in diagnostics.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum


class ActivityKind(StrEnum):
    STAGE = "stage"
    RETRY = "retry"
    WARNING = "warning"
    ERROR = "error"
    DETAIL = "detail"


class ActivityMessage(str):
    def __new__(cls, text, kind=ActivityKind.STAGE, *, batch=None, occurred_at=None):
        instance = super().__new__(cls, text)
        instance.kind = ActivityKind(kind)
        instance.batch = batch
        instance.occurred_at = occurred_at or datetime.now().astimezone()
        return instance

    def with_batch(self, batch):
        return ActivityMessage(self, self.kind, batch=batch, occurred_at=self.occurred_at)


def batch_message(index: int, text: str) -> ActivityMessage:
    """Attach the translator's zero-based batch ID without flattening typed messages."""
    if isinstance(text, ActivityMessage):
        return text.with_batch(index if index >= 0 else None)
    return ActivityMessage(text, batch=index if index >= 0 else None)
