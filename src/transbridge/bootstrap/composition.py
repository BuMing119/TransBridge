"""Single construction graph for a TransBridge process."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from transbridge.application.capabilities import CapabilityRegistry, CapabilityReport
from transbridge.application.ports import ClosablePort, closeables
from transbridge.application.tasks import (
    BoundedThreadPoolBackend,
    FilesystemCheckpointCatalog,
    FilesystemTaskHistoryPort,
    RecoveryCatalog,
    RecoveryExpectationRegistry,
    TaskHistoryRecorder,
    TaskRetryIntentRegistry,
    TaskRuntime,
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
    persistence_use_cases = {
        "persistence_v2": persistence,
        "project_lifecycle": persistence.project_lifecycle,
        "project_provisioning": persistence.project_provisioning,
        "project_remote_bindings": persistence.project_remote_bindings,
        "project_catalog": persistence.project_catalog,
        "project_catalog_repair_report": persistence.project_catalog_repair_report,
        "gui_project_commands": persistence.gui_project_commands,
        "current_project_opener": persistence.current_project_opener,
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
    }
    for name, use_case in persistence_use_cases.items():
        if name not in runtime_use_cases.names():
            runtime_use_cases.register(name, use_case)
    task_history_recorder.start()
    return AppRuntime(
        settings=runtime_settings,
        capabilities=runtime_capabilities,
        ports=runtime_ports,
        use_cases=runtime_use_cases,
        tasks=tasks,
        resources=closeables(resources),
        internal_resources=closeables((persistence, task_history_recorder)),
    )
