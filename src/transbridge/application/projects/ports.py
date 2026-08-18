"""Ports required by the Project lifecycle application service."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from transbridge.application.contracts import RequestContext

from .models import (
    ActiveProject,
    LifecycleActivation,
    LifecycleLease,
    LifecycleSave,
    LifecycleSnapshot,
    TransitionTarget,
)


@runtime_checkable
class CandidateLoaderPort(Protocol):
    def prepare_candidate(
        self,
        target: TransitionTarget,
        context: RequestContext,
    ) -> ActiveProject | None: ...


@runtime_checkable
class LifecycleLeasePort(Protocol):
    def acquire(self, target: TransitionTarget, context: RequestContext) -> tuple[LifecycleLease, ...]: ...

    def release(self, leases: tuple[LifecycleLease, ...]) -> None: ...


@runtime_checkable
class LifecycleUnitOfWorkPort(Protocol):
    def stage_save(self, save: LifecycleSave) -> None: ...

    def stage_activate(self, activation: LifecycleActivation) -> None: ...

    def stage_snapshot(self, snapshot: LifecycleSnapshot) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


@runtime_checkable
class LifecycleUnitOfWorkFactoryPort(Protocol):
    def begin(self) -> LifecycleUnitOfWorkPort: ...


class NullLifecycleLeasePort:
    def acquire(self, target: TransitionTarget, context: RequestContext) -> tuple[LifecycleLease, ...]:
        return ()

    def release(self, leases: tuple[LifecycleLease, ...]) -> None:
        return None


__all__ = [
    "CandidateLoaderPort",
    "LifecycleLeasePort",
    "LifecycleUnitOfWorkFactoryPort",
    "LifecycleUnitOfWorkPort",
    "NullLifecycleLeasePort",
]
