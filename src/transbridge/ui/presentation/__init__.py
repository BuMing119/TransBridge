"""Lightweight presentation contracts used by feature-local UI slices.

The package deliberately has no PyQt dependency.  Concrete widgets belong to
their feature packages and are assembled by a facade or shell.
"""

from .contracts import Binding, BusyState, ViewPort
from .messages import MessageSeverity, UiMessage
from .subscriptions import (
    CallbackSubscription,
    Subscription,
    SubscriptionCloseError,
    SubscriptionGroup,
)
from .task_projection import (
    TaskProjectionBinding,
    TaskProjectionReducer,
    TaskProjectionReduction,
)

__all__ = [
    "Binding",
    "BusyState",
    "CallbackSubscription",
    "MessageSeverity",
    "Subscription",
    "SubscriptionCloseError",
    "SubscriptionGroup",
    "TaskProjectionBinding",
    "TaskProjectionReducer",
    "TaskProjectionReduction",
    "UiMessage",
    "ViewPort",
]
