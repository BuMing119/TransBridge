"""Headless Project/Variant lifecycle application surface."""

from .catalog import (
    VariantDescriptor,
    project_with_added_variant,
    project_without_variant,
    variant_catalog,
)
from .gui_facade import GuiProjectCommandFacade
from .legacy import LegacyProjectLifecycleAdapter
from .lifecycle import ProjectLifecycleService
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
    project_with_active_variant,
)
from .ports import (
    CandidateLoaderPort,
    LifecycleLeasePort,
    LifecycleUnitOfWorkFactoryPort,
    LifecycleUnitOfWorkPort,
    NullLifecycleLeasePort,
)
from .provisioning import (
    PreparedProjectSource,
    PreparedSourceHydration,
    ProjectProvisioningCommit,
    ProjectProvisioningHydration,
    ProjectProvisioningHydrationResult,
    ProjectProvisioningIdentityPort,
    ProjectProvisioningLifecyclePort,
    ProjectProvisioningPreview,
    ProjectProvisioningRequest,
    ProjectProvisioningService,
    ProjectSourcePreparationPort,
    ProjectSourceRequest,
)
from .recent_catalog import ProjectCatalogEntry, ProjectCatalogQuery, ProjectCatalogSnapshot

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
    "PreparedProjectSource",
    "PreparedSourceHydration",
    "ProjectProvisioningCommit",
    "ProjectProvisioningIdentityPort",
    "ProjectProvisioningLifecyclePort",
    "ProjectProvisioningHydration",
    "ProjectProvisioningHydrationResult",
    "ProjectProvisioningPreview",
    "ProjectProvisioningRequest",
    "ProjectProvisioningService",
    "ProjectCatalogEntry",
    "ProjectCatalogQuery",
    "ProjectCatalogSnapshot",
    "ProjectSourcePreparationPort",
    "ProjectSourceRequest",
    "ProjectLifecycleService",
    "GuiProjectCommandFacade",
    "TransitionTarget",
    "VariantDescriptor",
    "project_with_added_variant",
    "project_with_active_variant",
    "project_without_variant",
    "variant_catalog",
]
