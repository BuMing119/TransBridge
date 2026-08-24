"""Single-owner dispatch for catalog intents across shell entry surfaces."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .action_catalog import (
    DEFAULT_ACTION_CATALOG,
    ActionAvailability,
    ActionCatalog,
    DangerLevel,
    IntentId,
)

IntentHandler = Callable[[Mapping[str, str]], object]
AvailabilityProvider = Callable[[], tuple[bool, str | None]]


@dataclass(frozen=True, slots=True)
class IntentDispatchResult:
    intent_id: IntentId
    accepted: bool
    reason: str | None = None
    requires_confirmation: bool = False
    value: object = None


class IntentRouter:
    """Route every menu, page and palette entry to one registered handler."""

    def __init__(self, catalog: ActionCatalog = DEFAULT_ACTION_CATALOG) -> None:
        self._catalog = catalog
        self._handlers: dict[IntentId, IntentHandler] = {}
        self._availability: dict[IntentId, AvailabilityProvider] = {}
        self._closed = False

    def register(
        self,
        intent_id: IntentId,
        handler: IntentHandler,
        *,
        availability: AvailabilityProvider | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("intent router is closed")
        self._catalog.get(intent_id)
        if intent_id in self._handlers and self._handlers[intent_id] is not handler:
            raise ValueError(f"intent already has an owner: {intent_id.value}")
        self._handlers[intent_id] = handler
        if availability is not None:
            self._availability[intent_id] = availability

    def availability(self, intent_id: IntentId) -> ActionAvailability:
        descriptor = self._catalog.get(intent_id)
        if self._closed:
            return ActionAvailability(descriptor, False, "当前窗口已关闭")
        if intent_id not in self._handlers:
            return ActionAvailability(descriptor, False, "此功能尚未接入当前界面")
        provider = self._availability.get(intent_id)
        enabled, reason = (True, None) if provider is None else provider()
        return ActionAvailability(descriptor, enabled, reason)

    def all_availability(self) -> tuple[ActionAvailability, ...]:
        return tuple(self.availability(item.intent_id) for item in self._catalog.all())

    def dispatch(
        self,
        intent_id: IntentId | str,
        payload: Mapping[str, str] | None = None,
        *,
        confirmed: bool = False,
    ) -> IntentDispatchResult:
        try:
            resolved = intent_id if isinstance(intent_id, IntentId) else IntentId(intent_id)
        except ValueError:
            raise KeyError(f"unknown intent: {intent_id}") from None
        availability = self.availability(resolved)
        if not availability.enabled:
            return IntentDispatchResult(resolved, False, availability.reason)
        descriptor = availability.descriptor
        if descriptor.danger is not DangerLevel.SAFE and not confirmed:
            return IntentDispatchResult(
                resolved,
                False,
                "该操作需要先确认影响范围",
                requires_confirmation=True,
            )
        value = self._handlers[resolved](MappingProxyType(dict(payload or {})))
        return IntentDispatchResult(resolved, True, value=value)

    def close(self) -> None:
        self._closed = True
        self._handlers.clear()
        self._availability.clear()


__all__ = [
    "AvailabilityProvider",
    "IntentDispatchResult",
    "IntentHandler",
    "IntentRouter",
]
