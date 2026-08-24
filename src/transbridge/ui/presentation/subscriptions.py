"""Idempotent ownership helpers for callbacks, signals and event listeners."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol


class Subscription(Protocol):
    def close(self) -> None: ...


class CallbackSubscription:
    """Run a detach callback at most once."""

    __slots__ = ("_callback", "_closed")

    def __init__(self, callback: Callable[[], None]) -> None:
        self._callback: Callable[[], None] | None = callback
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        callback, self._callback = self._callback, None
        if callback is not None:
            callback()


class SubscriptionCloseError(RuntimeError):
    """Aggregates detach failures after every subscription was attempted."""

    def __init__(self, errors: tuple[BaseException, ...]) -> None:
        self.errors = errors
        super().__init__(f"failed to close {len(errors)} subscription(s)")


class SubscriptionGroup:
    """Own subscriptions and close them in reverse registration order."""

    __slots__ = ("_subscriptions", "_closed")

    def __init__(self, subscriptions: Iterable[Subscription] = ()) -> None:
        self._subscriptions = list(subscriptions)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def add(self, subscription: Subscription) -> Subscription:
        if self._closed:
            subscription.close()
            return subscription
        self._subscriptions.append(subscription)
        return subscription

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        while self._subscriptions:
            subscription = self._subscriptions.pop()
            try:
                subscription.close()
            except BaseException as error:  # cleanup must attempt every owner
                errors.append(error)
        if errors:
            raise SubscriptionCloseError(tuple(errors))
