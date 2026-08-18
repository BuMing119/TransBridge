"""Minimal headless adapters used by the default composition root."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from transbridge.application.contracts import RequestContext


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    def new_id(self) -> str:
        return str(uuid4())


class NullSecretStore:
    """Safe default: capabilities requiring credentials remain unavailable."""

    def has_secret(self, name: str, context: RequestContext) -> bool:
        del name, context
        return False

    def get_secret(self, name: str, context: RequestContext) -> str | None:
        del name, context
        return None


class DenyByDefaultSecurity:
    def authorize(self, context: RequestContext, action: str, resource: str | None = None) -> bool:
        del resource
        return action in context.permissions
