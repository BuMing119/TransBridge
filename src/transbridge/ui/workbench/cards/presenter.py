"""Shared presentation state for Workbench operation cards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class OperationContextPort(Protocol):
    @property
    def slots(self) -> dict: ...

    @property
    def collection(self): ...

    @property
    def current_project(self): ...


@dataclass(frozen=True, slots=True)
class OperationCardState:
    batch_available: bool
    has_collection: bool
    has_project: bool


class OperationCardPresenter:
    """Map context projection to small card state without copying collections."""

    def __init__(self, context: OperationContextPort) -> None:
        self._context = context

    @property
    def batch_available(self) -> bool:
        return len(self._context.slots) > 1

    def state(self) -> OperationCardState:
        return OperationCardState(
            batch_available=self.batch_available,
            has_collection=self._context.collection is not None,
            has_project=self._context.current_project is not None,
        )
