"""Production composition for persistence V2 lifecycle and projections."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from transbridge.application.projections import (
    ProjectionStore,
    ProjectProjectionPublisher,
    SessionProjectionPublisher,
)
from transbridge.application.projects import GuiProjectCommandFacade, ProjectLifecycleService
from transbridge.application.sessions import GuiSessionCommandFacade, SessionLifecycleService
from transbridge.persistence.current_project import CurrentProjectOpener
from transbridge.persistence.project_lifecycle_loader import V2ProjectCandidateLoader
from transbridge.persistence.project_lifecycle_uow import RepositoryLifecycleUnitOfWorkFactory
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
    gui_project_commands: GuiProjectCommandFacade
    current_project_opener: CurrentProjectOpener
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
) -> PersistenceV2Services:
    resolved_root = str(Path(root).resolve(strict=False))
    adapter = filesystem or OsPersistenceFilesystem()
    projects = ProjectRepository(resolved_root, adapter)
    variants = VariantRepository(resolved_root, adapter)
    sessions = SessionRepository(resolved_root, adapter)
    session_catalog = SessionCatalogRepository(resolved_root, adapter)
    baselines = BaselineRegistry()
    identities = LegacyIdentityRegistry()

    project_projection = ProjectionStore()
    project_publisher = ProjectProjectionPublisher(project_projection)
    project_store = ProjectLifecycleTransactionStore(
        resolved_root,
        adapter,
        projects,
        variants,
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
    gui_project_commands = GuiProjectCommandFacade(
        project_lifecycle,
        identities,
        project_publisher.rebuild,
        projects=projects,
        variants=variants,
        baselines=baselines,
        id_factory=id_factory,
    )
    current_project_opener = CurrentProjectOpener(
        resolved_root,
        projects,
        variants,
        baselines,
        gui_project_commands,
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
        resolved_root,
        adapter,
        projects,
        variants,
        sessions,
        session_catalog,
        baselines,
        identities,
        project_lifecycle,
        gui_project_commands,
        current_project_opener,
        session_lifecycle,
        gui_session_commands,
        project_projection,
        session_projection,
    )


__all__ = ["PersistenceV2Services", "build_persistence_v2_services"]
