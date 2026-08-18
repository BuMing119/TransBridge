"""Single construction graph for a TransBridge process."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from transbridge.application.capabilities import CapabilityRegistry, CapabilityReport
from transbridge.application.ports import ClosablePort, closeables
from transbridge.application.tasks import BoundedThreadPoolBackend, TaskRuntime
from transbridge.application.use_cases import ValidateContextUseCase
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
    persistence_root = runtime_settings.get("persistence_v2_root")
    if persistence_root is None:
        persistence_root = Path(get_data_dir())
    persistence = build_persistence_v2_services(
        persistence_root,
        id_factory=runtime_ports.ids.new_id,
        timestamp_factory=lambda: runtime_ports.clock.now().isoformat(),
    )
    persistence_use_cases = {
        "persistence_v2": persistence,
        "project_lifecycle": persistence.project_lifecycle,
        "gui_project_commands": persistence.gui_project_commands,
        "current_project": persistence.current_project,
        "session_lifecycle": persistence.session_lifecycle,
        "gui_session_commands": persistence.gui_session_commands,
        "project_projection": persistence.project_projection,
        "session_projection": persistence.session_projection,
        "source_baselines": persistence.baselines,
        "legacy_identity_mappings": persistence.legacy_identities,
    }
    for name, use_case in persistence_use_cases.items():
        if name not in runtime_use_cases.names():
            runtime_use_cases.register(name, use_case)
    return AppRuntime(
        settings=runtime_settings,
        capabilities=runtime_capabilities,
        ports=runtime_ports,
        use_cases=runtime_use_cases,
        tasks=tasks,
        resources=closeables(resources),
        internal_resources=closeables((persistence,)),
    )
