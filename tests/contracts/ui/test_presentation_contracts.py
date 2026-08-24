from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from transbridge.ui.presentation import (
    BusyState,
    CallbackSubscription,
    MessageSeverity,
    SubscriptionCloseError,
    SubscriptionGroup,
    UiMessage,
)


def test_message_and_busy_state_are_immutable_and_validated() -> None:
    message = UiMessage("parse.failed", "解析失败", MessageSeverity.ERROR, retryable=True)
    state = BusyState(True, "parse", 1, 2, cancellable=True)

    with pytest.raises(FrozenInstanceError):
        message.text = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        state.active = False  # type: ignore[misc]
    with pytest.raises(ValueError):
        BusyState(True, "parse", 3, 2)


def test_callback_subscription_closes_at_most_once() -> None:
    calls: list[str] = []
    subscription = CallbackSubscription(lambda: calls.append("closed"))

    subscription.close()
    subscription.close()

    assert calls == ["closed"]
    assert subscription.closed


def test_group_closes_every_subscription_in_reverse_order_and_aggregates_errors() -> None:
    calls: list[str] = []

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("detach failed")

    group = SubscriptionGroup([CallbackSubscription(lambda: calls.append("first")), CallbackSubscription(fail)])

    with pytest.raises(SubscriptionCloseError) as error:
        group.close()

    assert calls == ["fail", "first"]
    assert len(error.value.errors) == 1
    group.close()


def test_adding_to_closed_group_detaches_immediately() -> None:
    calls: list[str] = []
    group = SubscriptionGroup()
    group.close()

    group.add(CallbackSubscription(lambda: calls.append("late")))

    assert calls == ["late"]
