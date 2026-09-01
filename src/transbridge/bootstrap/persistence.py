"""Production composition for persistence V2 lifecycle and projections."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path

from transbridge.application.projections import (
    ProjectionStore,
    ProjectProjectionPublisher,
    SessionProjectionPublisher,
)
from transbridge.application.projects import (
    GuiProjectCommandFacade,
    ProjectLifecycleService,
    ProjectManagementCommands,
    ProjectProvisioningService,
    ProjectRemoteBindingService,
    ProjectSourceMutationService,
    ProjectSourcePreparationPort,
)
from transbridge.application.projects.snapshots import ProjectSnapshotCommands
from transbridge.application.sessions import GuiSessionCommandFacade, SessionLifecycleService
from transbridge.persistence.current_project import CurrentProjectOpener
from transbridge.persistence.project_archive import ProjectArchiveService
from transbridge.persistence.project_catalog import V2ProjectCatalog
from transbridge.persistence.project_catalog_repair import (
    ProjectCatalogRepairReport,
    ProjectCatalogRepairService,
    ProjectCatalogRepairStatus,
)
from transbridge.persistence.project_lifecycle_loader import V2ProjectCandidateLoader
from transbridge.persistence.project_lifecycle_uow import RepositoryLifecycleUnitOfWorkFactory
from transbridge.persistence.project_management import ProjectManagementStore
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_snapshots import ProjectSnapshotRepository
from transbridge.persistence.session_lifecycle import (
    SessionUnitOfWorkFactory,
    V2SessionSnapshotRepository,
)
from transbridge.persistence.v2 import (
    OsPersistenceFilesystem,
    PersistenceFilesystemPort,
    ProjectRepository,
    SessionRepository,
    VariantRepository,
)
from transbridge.persistence.v2.baselines import BaselineRegistry, LegacyIdentityRegistry
from transbridge.persistence.v2.lifecycle_transactions import (
    ProjectLifecycleTransactionStore,
    SessionLifecycleTransactionStore,
)
from transbridge.persistence.v2.session_catalog import SessionCatalogRepository

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PersistenceV2Services:
    root: str
    filesystem: PersistenceFilesystemPort
    projects: ProjectRepository
    variants: VariantRepository
    sessions: SessionRepository
    session_catalog: SessionCatalogRepository
    baselines: BaselineRegistry
    legacy_identities: LegacyIdentityRegistry
    project_lifecycle: ProjectLifecycleService
    project_management: ProjectManagementCommands
    project_provisioning: ProjectProvisioningService
    project_remote_bindings: ProjectRemoteBindingService
    project_catalog: V2ProjectCatalog
    project_catalog_repair_report: ProjectCatalogRepairReport
    gui_project_commands: GuiProjectCommandFacade
    current_project_opener: CurrentProjectOpener
    project_snapshots: ProjectSnapshotCommands
    project_archive: ProjectArchiveService
    session_lifecycle: SessionLifecycleService
    gui_session_commands: GuiSessionCommandFacade
    project_projection: ProjectionStore
    session_projection: ProjectionStore

    def close(self) -> None:
        self.project_projection.close()
        self.session_projection.close()
        self.baselines.close()
        self.legacy_identities.close()


def build_persistence_v2_services(
    root: str | os.PathLike[str],
    *,
    id_factory,
    timestamp_factory,
    filesystem: PersistenceFilesystemPort | None = None,
    source_preparer: ProjectSourcePreparationPort | None = None,
) -> PersistenceV2Services:
    resolved_root = str(Path(root).resolve(strict=False))
    adapter = filesystem or OsPersistenceFilesystem()
    projects = ProjectRepository(resolved_root, adapter)
    variants = VariantRepository(resolved_root, adapter)
    sessions = SessionRepository(resolved_root, adapter)
    session_catalog = SessionCatalogRepository(resolved_root, adapter)
    baselines = BaselineRegistry()
    identities = LegacyIdentityRegistry()
    project_catalog_repair = ProjectCatalogRepairService(resolved_root, adapter, projects)
    project_catalog_repair_report = project_catalog_repair.repair_if_missing()
    if project_catalog_repair_report.status is ProjectCatalogRepairStatus.REBUILT:
        logger.info(
            "Rebuilt missing Project catalog from %d verified records; skipped=%d",
            project_catalog_repair_report.recovered_count,
            project_catalog_repair_report.skipped_count,
        )
    if project_catalog_repair_report.diagnostics:
        logger.warning(
            "Project catalog startup repair diagnostics: status=%s recovered=%d skipped=%d diagnostics=%s",
            project_catalog_repair_report.status.value,
            project_catalog_repair_report.recovered_count,
            project_catalog_repair_report.skipped_count,
            tuple(diagnostic.code for diagnostic in project_catalog_repair_report.diagnostics),
        )

    project_projection = ProjectionStore()
    project_publisher = ProjectProjectionPublisher(project_projection)
    project_store = ProjectLifecycleTransactionStore(
        resolved_root,
        adapter,
        projects,
        variants,
        baselines,
    )
    project_uow = RepositoryLifecycleUnitOfWorkFactory(project_store, id_factory)
    project_loader = V2ProjectCandidateLoader(projects, variants, baselines.provide)
    project_lifecycle = ProjectLifecycleService(
        project_loader,
        project_uow,
        token_factory=id_factory,
        event_publisher=project_publisher,
    )
    project_publisher.bind(project_lifecycle)
    project_management_store = ProjectManagementStore(
        resolved_root,
        adapter,
        projects,
        variants,
        token_factory=id_factory,
    )
    project_management = ProjectManagementCommands(project_lifecycle, project_management_store, baselines)
    snapshot_repository = ProjectSnapshotRepository(resolved_root, adapter)
    project_snapshots = ProjectSnapshotCommands(project_lifecycle, snapshot_repository)
    resolved_source_preparer = source_preparer or TranslationIoProjectSourcePreparer()
    project_archive = ProjectArchiveService(
        resolved_root,
        adapter,
        projects,
        variants,
        project_lifecycle,
        snapshot_repository,
        id_factory=id_factory,
        source_preparer=resolved_source_preparer,
    )
    project_provisioning = ProjectProvisioningService(
        project_lifecycle,
        resolved_source_preparer,
        project_store,
        id_factory=id_factory,
        token_factory=id_factory,
    )
    project_source_mutations = ProjectSourceMutationService(
        project_lifecycle,
        baselines,
        resolved_source_preparer,
    )
    project_remote_bindings = ProjectRemoteBindingService(project_lifecycle)
    project_catalog = V2ProjectCatalog(resolved_root, adapter, projects)
    gui_project_commands = GuiProjectCommandFacade(
        project_lifecycle,
        identities,
        project_publisher.rebuild,
        projects=projects,
        variants=variants,
        baselines=baselines,
        id_factory=id_factory,
        provisioning=project_provisioning,
        source_mutations=project_source_mutations,
    )
    current_project_opener = CurrentProjectOpener(
        resolved_root,
        projects,
        variants,
        baselines,
        gui_project_commands,
        source_preparer=resolved_source_preparer,
    )

    session_projection = ProjectionStore()
    session_publisher = SessionProjectionPublisher(session_projection)
    session_store = SessionLifecycleTransactionStore(resolved_root, adapter)
    session_uow = SessionUnitOfWorkFactory(session_store, id_factory)
    session_lifecycle = SessionLifecycleService(
        V2SessionSnapshotRepository(sessions),
        session_uow,
        token_factory=id_factory,
        projection=session_publisher,
    )
    session_publisher.bind(session_lifecycle)
    gui_session_commands = GuiSessionCommandFacade(
        session_lifecycle,
        sessions,
        session_catalog,
        id_factory=id_factory,
        timestamp_factory=timestamp_factory,
    )

    return PersistenceV2Services(
        root=resolved_root,
        filesystem=adapter,
        projects=projects,
        variants=variants,
        sessions=sessions,
        session_catalog=session_catalog,
        baselines=baselines,
        legacy_identities=identities,
        project_lifecycle=project_lifecycle,
        project_management=project_management,
        project_provisioning=project_provisioning,
        project_remote_bindings=project_remote_bindings,
        project_catalog=project_catalog,
        project_catalog_repair_report=project_catalog_repair_report,
        gui_project_commands=gui_project_commands,
        current_project_opener=current_project_opener,
        project_snapshots=project_snapshots,
        project_archive=project_archive,
        session_lifecycle=session_lifecycle,
        gui_session_commands=gui_session_commands,
        project_projection=project_projection,
        session_projection=session_projection,
    )


__all__ = ["PersistenceV2Services", "build_persistence_v2_services"]
