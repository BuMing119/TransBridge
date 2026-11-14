# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
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
from transbridge.application.projects import (
    GuiProjectCommandFacade,
    ProjectLifecycleService,
    ProjectManagementCommands,
    ProjectProvisioningService,
    ProjectRemoteBindingService,
    ProjectSourceMutationService,
    ProjectSourcePreparationPort,
)
from transbridge.application.projects.durable_variant import DurableVariantCommitService
from transbridge.application.projects.profile_entry_inputs import ProfileEntryInputReader
from transbridge.application.projects.snapshots import ProjectSnapshotCommands
from transbridge.application.projects.source_update import ProjectSourceUpdateService
from transbridge.application.projects.text_encoding import ProjectTextEncodingService
from transbridge.application.sessions import GuiSessionCommandFacade, SessionLifecycleService
from transbridge.config.text_encoding import TextEncodingDefaults
from transbridge.persistence.project_lifecycle_uow import RepositoryLifecycleUnitOfWorkFactory
from transbridge.persistence.project_provisioning import TranslationIoProjectSourcePreparer
from transbridge.persistence.project_store.archive import ProjectPackage
from transbridge.persistence.project_store.baselines import ProjectBaselineRegistry
from transbridge.persistence.project_store.catalog import ActiveProjectPreference, ProjectCatalog, ProjectDeletionStore
from transbridge.persistence.project_store.historical_restore import HistoricalSourceRestore
from transbridge.persistence.project_store.history import ProjectHistory
from transbridge.persistence.project_store.lifecycle import ProjectLifecycleStore
from transbridge.persistence.project_store.opening import StoredProjectOpener
from transbridge.persistence.project_store.records import CandidateLoader, WorkspaceProjects, WorkspaceVariants
from transbridge.persistence.project_store.source_reads import ProjectSourceReads
from transbridge.persistence.project_store.source_updates import ProjectSourceRevisionStore
from transbridge.persistence.session_lifecycle import (
    SessionUnitOfWorkFactory,
    V2SessionSnapshotRepository,
)
from transbridge.persistence.terminology.project_selection import ProjectTerminologySelection
from transbridge.persistence.v2 import (
    OsPersistenceFilesystem,
    PersistenceFilesystemPort,
    SessionRepository,
)
from transbridge.persistence.v2.baselines import BaselineRegistry
from transbridge.persistence.v2.lifecycle_transactions import SessionLifecycleTransactionStore
from transbridge.persistence.v2.session_catalog import SessionCatalogRepository


@dataclass(slots=True)
class PersistenceV2Services:
    root: str
    filesystem: PersistenceFilesystemPort
    projects: WorkspaceProjects
    variants: WorkspaceVariants
    sessions: SessionRepository
    session_catalog: SessionCatalogRepository
    baselines: BaselineRegistry
    project_lifecycle: ProjectLifecycleService
    project_management: ProjectManagementCommands
    project_provisioning: ProjectProvisioningService
    project_remote_bindings: ProjectRemoteBindingService
    project_catalog: ProjectCatalog
    gui_project_commands: GuiProjectCommandFacade
    current_project_opener: StoredProjectOpener
    project_snapshots: ProjectSnapshotCommands
    project_archive: ProjectPackage
    project_source_updates: ProjectSourceUpdateService
    project_text_encodings: ProjectTextEncodingService
    session_lifecycle: SessionLifecycleService
    gui_session_commands: GuiSessionCommandFacade
    project_projection: ProjectionStore
    session_projection: ProjectionStore

    def close(self) -> None:
        if self.gui_session_commands.assistant_requests is not None:
            self.gui_session_commands.assistant_requests.close()
        self.project_projection.close()
        self.session_projection.close()
        self.baselines.close()


def build_persistence_v2_services(
    root: str | os.PathLike[str],
    *,
    id_factory,
    timestamp_factory,
    filesystem: PersistenceFilesystemPort | None = None,
    source_preparer: ProjectSourcePreparationPort | None = None,
    text_encoding_defaults: TextEncodingDefaults | None = None,
) -> PersistenceV2Services:
    resolved_root = str(Path(root).resolve(strict=False))
    adapter = filesystem or OsPersistenceFilesystem()
    projects = WorkspaceProjects(resolved_root)
    variants = WorkspaceVariants(projects)
    sessions = SessionRepository(resolved_root, adapter)
    session_catalog = SessionCatalogRepository(resolved_root, adapter)
    baselines = ProjectBaselineRegistry(projects)
    active_project = ActiveProjectPreference(resolved_root, adapter)
    project_catalog = ProjectCatalog(projects, active_project)
    project_store = ProjectLifecycleStore(projects, variants, project_catalog, active_project, baselines)
    source_update_store = ProjectSourceRevisionStore(projects)

    project_projection = ProjectionStore()
    project_publisher = ProjectProjectionPublisher(project_projection)
    project_uow = RepositoryLifecycleUnitOfWorkFactory(project_store, id_factory)
    resolved_source_preparer = source_preparer or TranslationIoProjectSourcePreparer()
    project_loader = CandidateLoader(projects.candidate)
    project_lifecycle = ProjectLifecycleService(
        project_loader,
        project_uow,
        token_factory=id_factory,
        event_publisher=project_publisher,
    )
    project_publisher.bind(project_lifecycle)
    project_management_store = ProjectDeletionStore(projects, active_project)
    project_management = ProjectManagementCommands(project_lifecycle, project_management_store, baselines)
    snapshot_repository = ProjectHistory(projects)
    project_snapshots = ProjectSnapshotCommands(
        project_lifecycle,
        snapshot_repository,
        source_restore=HistoricalSourceRestore(projects, project_lifecycle, baselines),
    )
    project_archive = ProjectPackage(projects, project_catalog, project_lifecycle)
    project_provisioning = ProjectProvisioningService(
        project_lifecycle,
        resolved_source_preparer,
        project_store,
        id_factory=id_factory,
        token_factory=id_factory,
        text_encoding_defaults=(text_encoding_defaults or TextEncodingDefaults()).load,
    )
    project_source_mutations = ProjectSourceMutationService(
        project_lifecycle,
        baselines,
        resolved_source_preparer,
    )
    project_source_updates = ProjectSourceUpdateService(
        project_lifecycle,
        projects,
        source_update_store,
        baselines,
        resolved_source_preparer,
    )
    project_text_encodings = ProjectTextEncodingService(project_lifecycle, projects, source_update_store)
    project_remote_bindings = ProjectRemoteBindingService(project_lifecycle)
    from transbridge.application.projects.categorized_upload import ProjectCategorizedUploadService

    gui_project_commands = GuiProjectCommandFacade(
        project_lifecycle,
        project_publisher.rebuild,
        projects=projects,
        variants=variants,
        baselines=baselines,
        id_factory=id_factory,
        provisioning=project_provisioning,
        source_mutations=project_source_mutations,
        categorized_uploads=ProjectCategorizedUploadService(
            project_lifecycle,
            baselines,
            resolved_source_preparer,
            stored_sources=ProjectSourceReads(projects, project_lifecycle),
        ),
        durable_variant_commits=DurableVariantCommitService(
            project_lifecycle,
            project_uow,
            projects=projects,
            saved_candidate_verifier=lambda save: project_store.verify_saved(save),
        ),
        profile_variants=ProjectTerminologySelection(resolved_root),
        profile_entry_inputs=ProfileEntryInputReader(projects).read,
    )
    current_project_opener = StoredProjectOpener(
        projects, active_project, project_loader, baselines, gui_project_commands, project_publisher
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
    from transbridge.application.assistant_requests.round_undo import RoundUndoService
    from transbridge.application.assistant_requests.service import RequestService
    from transbridge.persistence.assistant_transcript_store import AssistantTranscriptStore

    assistant_requests = RequestService(
        session_lifecycle, transcript_store=AssistantTranscriptStore(str(resolved_root), adapter)
    )
    assistant_requests.undo = RoundUndoService(assistant_requests, project_lifecycle)
    assistant_requests.undo.file_backup_root = Path(resolved_root) / "assistant-undo-files"
    from transbridge.application.assistant_requests.deletion import deletion_preflight

    session_lifecycle.set_delete_preflight(deletion_preflight(assistant_requests))
    gui_session_commands = GuiSessionCommandFacade(
        session_lifecycle,
        sessions,
        session_catalog,
        id_factory=id_factory,
        timestamp_factory=timestamp_factory,
        assistant_requests=assistant_requests,
    )

    return PersistenceV2Services(
        root=resolved_root,
        filesystem=adapter,
        projects=projects,
        variants=variants,
        sessions=sessions,
        session_catalog=session_catalog,
        baselines=baselines,
        project_lifecycle=project_lifecycle,
        project_management=project_management,
        project_provisioning=project_provisioning,
        project_remote_bindings=project_remote_bindings,
        project_catalog=project_catalog,
        gui_project_commands=gui_project_commands,
        current_project_opener=current_project_opener,
        project_snapshots=project_snapshots,
        project_archive=project_archive,
        project_source_updates=project_source_updates,
        project_text_encodings=project_text_encodings,
        session_lifecycle=session_lifecycle,
        gui_session_commands=gui_session_commands,
        project_projection=project_projection,
        session_projection=session_projection,
    )


__all__ = ["PersistenceV2Services", "build_persistence_v2_services"]
