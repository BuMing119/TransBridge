"""Headless Project/Variant lifecycle application surface."""

from .legacy import LegacyProjectLifecycleAdapter
from .lifecycle import ProjectLifecycleService
from .gui_facade import GuiProjectCommandFacade
from .models import (
    ActiveProject,
    DirtyDecision,
    ExportRevisionLease,
    LifecycleActivation,
    LifecycleEvent,
    LifecycleLease,
    LifecycleSave,
    LifecycleSnapshot,
    PreparedTransition,
    TransitionTarget,
)
from .ports import (
    CandidateLoaderPort,
    LifecycleLeasePort,
    LifecycleUnitOfWorkFactoryPort,
    LifecycleUnitOfWorkPort,
    NullLifecycleLeasePort,
)

__all__ = [
    "ActiveProject",
    "CandidateLoaderPort",
    "DirtyDecision",
    "ExportRevisionLease",
    "LegacyProjectLifecycleAdapter",
    "LifecycleActivation",
    "LifecycleEvent",
    "LifecycleLease",
    "LifecycleLeasePort",
    "LifecycleSave",
    "LifecycleSnapshot",
    "LifecycleUnitOfWorkFactoryPort",
    "LifecycleUnitOfWorkPort",
    "NullLifecycleLeasePort",
    "PreparedTransition",
    "ProjectLifecycleService",
    "GuiProjectCommandFacade",
    "TransitionTarget",
]
