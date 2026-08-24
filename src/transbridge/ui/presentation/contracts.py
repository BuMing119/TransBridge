"""Qt-free contracts shared by presentation slices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from .messages import UiMessage


@dataclass(frozen=True, slots=True)
class BusyState:
    """A small immutable description of an in-progress UI operation."""

    active: bool
    operation: str = ""
    current: int | None = None
    total: int | None = None
    cancellable: bool = False

    def __post_init__(self) -> None:
        if self.current is not None and self.current < 0:
            raise ValueError("current must be non-negative")
        if self.total is not None and self.total < 0:
            raise ValueError("total must be non-negative")
        if self.current is not None and self.total is None:
            raise ValueError("total is required when current is provided")
        if self.current is not None and self.total is not None and self.current > self.total:
            raise ValueError("current cannot exceed total")


StateT = TypeVar("StateT", contravariant=True)


class ViewPort(Protocol[StateT]):
    """Minimum rendering surface implemented by a concrete feature View."""

    def render(self, state: StateT) -> None: ...

    def show_error(self, message: UiMessage) -> None: ...

    def set_busy(self, state: BusyState) -> None: ...


class Binding(Protocol):
    """Lifecycle contract for external-event to presentation adapters."""

    def start(self) -> None: ...

    def close(self) -> None: ...
