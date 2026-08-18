"""Headless Session aggregate, recovery, and lifecycle contracts."""

from .aggregate import EventApplication, SessionAggregate, SessionEventKind, SessionRuntimeEvent
from .gui_facade import GuiSessionCommandFacade, SessionCreateRepositoryPort
from .lifecycle import ActiveSession, PreparedSessionTransition, SessionLifecycleService
from .models import (
    ApprovalState,
    ControllerSnapshot,
    ControllerState,
    PendingApproval,
    RecoveryStatus,
    SessionJobRef,
    SessionSnapshot,
)
from .ports import (
    IdentitySessionReconciler,
    SessionReconcilerPort,
    SessionSnapshotRepositoryPort,
    SessionUnitOfWorkFactoryPort,
    SessionUnitOfWorkPort,
)
from .recovery import SessionRecoveryReconciler

__all__ = [
    "ActiveSession",
    "ApprovalState",
    "ControllerSnapshot",
    "ControllerState",
    "EventApplication",
    "GuiSessionCommandFacade",
    "IdentitySessionReconciler",
    "PendingApproval",
    "PreparedSessionTransition",
    "RecoveryStatus",
    "SessionAggregate",
    "SessionEventKind",
    "SessionJobRef",
    "SessionLifecycleService",
    "SessionReconcilerPort",
    "SessionRecoveryReconciler",
    "SessionRuntimeEvent",
    "SessionSnapshot",
    "SessionSnapshotRepositoryPort",
    "SessionCreateRepositoryPort",
    "SessionUnitOfWorkFactoryPort",
    "SessionUnitOfWorkPort",
]
