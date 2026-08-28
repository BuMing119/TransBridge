"""Production composition for the project terminology workbench."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from transbridge.application.contracts import RequestContext
from transbridge.application.io import default_format_catalog
from transbridge.application.projects import ProjectLifecycleService
from transbridge.application.terminology.effective import SnapshotEffectiveTerminologyPort
from transbridge.application.terminology.input_capture import BuildInputCaptureService
from transbridge.application.terminology.runtime import TerminologyTaskEntrypoint, TerminologyWorkloadRegistry
from transbridge.application.terminology.workloads import TerminologyWorkloadType
from transbridge.persistence.terminology import SqliteEffectiveTerminologySnapshotPort

from .terminology_storage import (
    FilesystemSourceLeases,
    FormatCapabilities,
    LifecycleCapture,
    ProductionState,
    ProductionTerminologyCommitPort,
    ProjectTerminologyRepositories,
    RepositoryBaselines,
)
from .terminology_workloads import (
    BuildRunner,
    ChangelogRunner,
    HistoryCompareRunner,
    ProductionTerminologyCommands,
    PublishRunner,
    ReportRunner,
)


@dataclass(frozen=True, slots=True)
class ProductionTerminologyComposition:
    repositories: ProjectTerminologyRepositories
    build_inputs: BuildInputCaptureService
    workloads: TerminologyWorkloadRegistry
    tasks: TerminologyTaskEntrypoint
    commands: ProductionTerminologyCommands
    commit_port: ProductionTerminologyCommitPort

    def services_for(self, context: RequestContext):
        from transbridge.ui.tools.terminology.presenter import TerminologyUiServices

        queries = None if context.project_id is None else self.repositories.for_project(context.project_id)
        return TerminologyUiServices(self.build_inputs, queries, self.commands, self.tasks.runtime)

    def effective_adapter(self, project_id: str, variant_id: str):
        """Create an adapter enabled by the existence of a published version."""

        del variant_id

        from transbridge.ai_translator.project_terminology_adapter import (
            ProjectTerminologyAdapter,
            PublishedEffectiveTerminologyGate,
        )

        repository = self.repositories.for_project(project_id)
        return ProjectTerminologyAdapter(
            SnapshotEffectiveTerminologyPort(SqliteEffectiveTerminologySnapshotPort(repository)),
            PublishedEffectiveTerminologyGate(
                lambda candidate_project, candidate_variant: (
                    repository.effective_version(candidate_project, candidate_variant) is not None
                )
            ),
        )


def build_production_terminology(
    *,
    root: str | Path,
    lifecycle: ProjectLifecycleService,
    task_runtime,
    ids,
    clock,
    max_unstreamed_source_count: int = 50,
    max_unstreamed_source_bytes: int = 64 * 1024 * 1024,
    max_unstreamed_total_bytes: int = 256 * 1024 * 1024,
) -> ProductionTerminologyComposition:
    repositories = ProjectTerminologyRepositories(root)
    leases = FilesystemSourceLeases(max_unstreamed_source_bytes=max_unstreamed_source_bytes)
    capabilities = FormatCapabilities(default_format_catalog())
    state = ProductionState()
    build_inputs = BuildInputCaptureService(
        LifecycleCapture(lifecycle),
        leases,
        capabilities,
        RepositoryBaselines(repositories),
        max_unstreamed_source_count=max_unstreamed_source_count,
        max_unstreamed_source_bytes=max_unstreamed_source_bytes,
        max_unstreamed_total_bytes=max_unstreamed_total_bytes,
    )
    commit_port = ProductionTerminologyCommitPort(lifecycle, repositories, state, leases)
    registry = TerminologyWorkloadRegistry()
    registry.bind(TerminologyWorkloadType.BUILD, BuildRunner(repositories, state, capabilities.catalog, ids))
    registry.bind(TerminologyWorkloadType.HISTORY_COMPARE, HistoryCompareRunner(repositories, state))
    registry.bind(TerminologyWorkloadType.PUBLISH, PublishRunner(repositories, state))
    registry.bind(TerminologyWorkloadType.REPORT_RENDER, ReportRunner(repositories, state))
    registry.bind(TerminologyWorkloadType.CHANGELOG_RENDER, ChangelogRunner(repositories, state))
    tasks = TerminologyTaskEntrypoint(task_runtime, registry, commit_port)
    commands = ProductionTerminologyCommands(
        tasks,
        repositories,
        repositories.paths,
        state,
        ids,
        clock,
        build_inputs,
        lifecycle,
    )
    return ProductionTerminologyComposition(
        repositories,
        build_inputs,
        registry,
        tasks,
        commands,
        commit_port,
    )


__all__ = [
    "ProductionTerminologyComposition",
    "ProductionTerminologyCommands",
    "ProjectTerminologyRepositories",
    "build_production_terminology",
]
