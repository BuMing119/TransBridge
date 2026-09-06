"""Single construction graph for a TransBridge process."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from transbridge.application.capabilities import (
    CapabilityId,
    CapabilityRegistry,
    CapabilityReport,
    CapabilityState,
)
from transbridge.application.ports import ClosablePort, closeables
from transbridge.application.tasks import (
    BoundedThreadPoolBackend,
    FilesystemCheckpointCatalog,
    FilesystemTaskHistoryPort,
    RecoveryCatalog,
    RecoveryExpectationRegistry,
    TaskCenterActions,
    TaskHistoryNavigationRegistry,
    TaskHistoryRecorder,
    TaskRecoveryIntentRegistry,
    TaskRetryIntentRegistry,
    TaskRuntime,
)
from transbridge.application.terminology.changelog_queries import ChangeLogQueryService
from transbridge.application.terminology.report_queries import TerminologyReportQueryService
from transbridge.application.terminology.runtime import (
    TerminologyTaskEntrypoint,
    TerminologyWorkloadRegistry,
    UnavailableTerminologyCommitPort,
)
from transbridge.application.use_cases import ValidateContextUseCase
from transbridge.config import ConfigRepository, UiPreferenceRepository, default_config_repository
from transbridge.config.paths import get_data_dir

from .adapters import DenyByDefaultSecurity, NullSecretStore, SystemClock, UuidGenerator
from .persistence import build_persistence_v2_services
from .runtime import AppRuntime, RuntimePorts, UseCaseRegistry


def build_runtime(
    settings: Mapping[str, Any] | None = None,
    *,
    capabilities: CapabilityRegistry | Iterable[CapabilityReport] | None = None,
    ports: RuntimePorts | None = None,
    use_cases: Mapping[str, Any] | None = None,
    task_runtime: TaskRuntime | None = None,
    resources: Iterable[ClosablePort] = (),
) -> AppRuntime:
    """Build a fully isolated runtime without importing GUI frameworks."""

    runtime_ports = ports or RuntimePorts(
        clock=SystemClock(),
        ids=UuidGenerator(),
        secrets=NullSecretStore(),
        security=DenyByDefaultSecurity(),
    )
    capability_reports = capabilities.snapshot() if isinstance(capabilities, CapabilityRegistry) else capabilities or ()
    runtime_capabilities = CapabilityRegistry(capability_reports)
    runtime_use_cases = UseCaseRegistry(use_cases)
    if "terminology_repository" in runtime_use_cases.names():
        terminology_repository = runtime_use_cases.resolve("terminology_repository")
        if "terminology_artifact_ledger" not in runtime_use_cases.names():
            runtime_use_cases.register("terminology_artifact_ledger", terminology_repository)
        if "terminology_report_queries" not in runtime_use_cases.names():
            runtime_use_cases.register(
                "terminology_report_queries",
                TerminologyReportQueryService(terminology_repository),
            )
        if "terminology_changelog_queries" not in runtime_use_cases.names():
            runtime_use_cases.register(
                "terminology_changelog_queries",
                ChangeLogQueryService(getattr(terminology_repository, "changelogs", terminology_repository)),
            )
    if "validate_context" not in runtime_use_cases.names():
        runtime_use_cases.register("validate_context", ValidateContextUseCase(runtime_ports.secrets))
    tasks = task_runtime or TaskRuntime(
        id_generator=runtime_ports.ids,
        clock=runtime_ports.clock,
        backend=BoundedThreadPoolBackend(max_workers=3),
    )
    runtime_settings = dict(settings or {})
    if "ui_preferences" not in runtime_use_cases.names():
        ui_config_path = runtime_settings.get("ui_config_path")
        config_repository = (
            default_config_repository()
            if ui_config_path is None
            else ConfigRepository(
                path=ui_config_path,
                legacy_path=runtime_settings.get("legacy_config_path", ui_config_path),
            )
        )
        runtime_use_cases.register("ui_preferences", UiPreferenceRepository(config_repository))
    persistence_root = runtime_settings.get("persistence_v2_root")
    if persistence_root is None:
        persistence_root = Path(get_data_dir())
    persistence = build_persistence_v2_services(
        persistence_root,
        id_factory=runtime_ports.ids.new_id,
        timestamp_factory=lambda: runtime_ports.clock.now().isoformat(),
    )
    task_data_root = Path(persistence_root) / "task-activity"
    task_history = FilesystemTaskHistoryPort(task_data_root)
    task_history_recorder = TaskHistoryRecorder(tasks, task_history)
    task_recovery_expectations = RecoveryExpectationRegistry()
    task_recovery = RecoveryCatalog(
        FilesystemCheckpointCatalog(task_data_root / "checkpoints"),
        task_recovery_expectations,
    )
    task_retry_intents = TaskRetryIntentRegistry()
    task_recovery_intents = TaskRecoveryIntentRegistry()
    task_history_navigators = TaskHistoryNavigationRegistry()
    existing_use_cases = set(runtime_use_cases.names())
    task_center_actions = TaskCenterActions(
        runtime_use_cases.resolve("task_history") if "task_history" in existing_use_cases else task_history,
        runtime_use_cases.resolve("task_recovery") if "task_recovery" in existing_use_cases else task_recovery,
        (
            runtime_use_cases.resolve("task_retry_intents")
            if "task_retry_intents" in existing_use_cases
            else task_retry_intents
        ),
        (
            runtime_use_cases.resolve("task_recovery_intents")
            if "task_recovery_intents" in existing_use_cases
            else task_recovery_intents
        ),
        (
            runtime_use_cases.resolve("task_history_navigators")
            if "task_history_navigators" in existing_use_cases
            else task_history_navigators
        ),
    )
    persistence_use_cases = {
        "persistence_v2": persistence,
        "project_lifecycle": persistence.project_lifecycle,
        "project_management": persistence.project_management,
        "project_provisioning": persistence.project_provisioning,
        "project_remote_bindings": persistence.project_remote_bindings,
        "project_catalog": persistence.project_catalog,
        "project_catalog_repair_report": persistence.project_catalog_repair_report,
        "gui_project_commands": persistence.gui_project_commands,
        "current_project_opener": persistence.current_project_opener,
        "project_snapshots": persistence.project_snapshots,
        "project_archive": persistence.project_archive,
        "session_lifecycle": persistence.session_lifecycle,
        "gui_session_commands": persistence.gui_session_commands,
        "project_projection": persistence.project_projection,
        "session_projection": persistence.session_projection,
        "source_baselines": persistence.baselines,
        "legacy_identity_mappings": persistence.legacy_identities,
        "task_history": task_history,
        "task_recovery": task_recovery,
        "task_recovery_expectations": task_recovery_expectations,
        "task_retry_intents": task_retry_intents,
        "task_recovery_intents": task_recovery_intents,
        "task_history_navigators": task_history_navigators,
        "task_center_actions": task_center_actions,
    }
    for name, use_case in persistence_use_cases.items():
        if name not in runtime_use_cases.names():
            runtime_use_cases.register(name, use_case)
    terminology_resource = None
    if "terminology_repository" not in runtime_use_cases.names():
        from .terminology import build_production_terminology

        terminology = build_production_terminology(
            root=persistence_root,
            lifecycle=persistence.project_lifecycle,
            task_runtime=tasks,
            ids=runtime_ports.ids,
            clock=runtime_ports.clock,
            max_unstreamed_source_count=int(runtime_settings.get("terminology_max_unstreamed_source_count", 50)),
            max_unstreamed_source_bytes=int(
                runtime_settings.get("terminology_max_unstreamed_source_bytes", 64 * 1024 * 1024)
            ),
            max_unstreamed_total_bytes=int(
                runtime_settings.get("terminology_max_unstreamed_total_bytes", 256 * 1024 * 1024)
            ),
        )
        production_use_cases = {
            "terminology_repository": terminology.repositories,
            "terminology_repository_factory": terminology.repositories,
            "terminology_build_input": terminology.build_inputs,
            "terminology_workloads": terminology.workloads,
            "terminology_tasks": terminology.tasks,
            "terminology_commit_port": terminology.commit_port,
            "terminology_ui_commands": terminology.commands,
            "terminology_ui_services_factory": terminology,
            "effective_terminology_factory": terminology,
            "terminology_profile_service_factory": terminology,
            "terminology_sync_service_factory": terminology,
        }
        for name, use_case in production_use_cases.items():
            runtime_use_cases.register(name, use_case)
        for capability_name in (
            "analysis-report",
            "draft-publish",
            "effective",
            "history-revert-changelog",
        ):
            runtime_capabilities.register(
                CapabilityReport(
                    CapabilityId(f"terminology.{capability_name}"),
                    CapabilityState.AVAILABLE,
                )
            )
        runtime_capabilities.register(
            CapabilityReport(
                CapabilityId("terminology.partial-publish"),
                CapabilityState.UNAVAILABLE,
                reasons=("部分完成的构建不支持发布。",),
                missing_prerequisites=("complete-build-required",),
            )
        )
        runtime_capabilities.register(
            CapabilityReport(
                CapabilityId("terminology.paratranz-sync"),
                CapabilityState.AVAILABLE,
            )
        )
        terminology_resource = terminology
    else:
        # Explicitly supplied repositories are a test/integration seam.  They
        # retain the historical fail-closed fallback unless the caller also
        # provides real runners and a business commit port.
        if "terminology_workloads" not in runtime_use_cases.names():
            runtime_use_cases.register("terminology_workloads", TerminologyWorkloadRegistry())
        if "terminology_tasks" not in runtime_use_cases.names():
            commit_port = (
                runtime_use_cases.resolve("terminology_commit_port")
                if "terminology_commit_port" in runtime_use_cases.names()
                else UnavailableTerminologyCommitPort()
            )
            runtime_use_cases.register(
                "terminology_tasks",
                TerminologyTaskEntrypoint(tasks, runtime_use_cases.resolve("terminology_workloads"), commit_port),
            )
    if "history_search" not in runtime_use_cases.names():
        from transbridge.translation_memory.manager import TranslationMemoryManager

        from .history_search import build_production_history_search

        translation_memory_root = runtime_settings.get("translation_memory_root")
        if translation_memory_root is None:
            translation_memory_root = TranslationMemoryManager().default_dir()
        history_search = build_production_history_search(
            persistence=persistence,
            task_runtime=tasks,
            translation_memory_root=translation_memory_root,
        )
        runtime_use_cases.register("history_search", history_search.query)
        runtime_use_cases.register("history_search_refresh", history_search.refresh)
        runtime_use_cases.register("history_search_tasks", history_search.tasks)
    task_history_recorder.start()
    return AppRuntime(
        settings=runtime_settings,
        capabilities=runtime_capabilities,
        ports=runtime_ports,
        use_cases=runtime_use_cases,
        tasks=tasks,
        resources=closeables(resources),
        internal_resources=closeables(
            (persistence, task_history_recorder)
            if terminology_resource is None
            else (persistence, task_history_recorder, terminology_resource)
        ),
    )
