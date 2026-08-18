"""Shared environment-to-runtime binding for headless process adapters."""

from __future__ import annotations

from collections.abc import Mapping
import os

from transbridge.application.capabilities import (
    CapabilityId,
    CapabilityReport,
    CapabilityState,
)
from transbridge.application.contracts import RequestContext
from transbridge.bootstrap import EntrypointBinding, RuntimePorts, bind_runtime, build_runtime
from transbridge.bootstrap.adapters import (
    DenyByDefaultSecurity,
    SystemClock,
    UuidGenerator,
)


class EnvironmentSecretStore:
    """Process-local secret port backed by explicitly named environment values."""

    def __init__(self, environ: Mapping[str, str]) -> None:
        self._environ = environ

    @staticmethod
    def _key(name: str) -> str:
        normalized = "".join(character if character.isalnum() else "_" for character in name)
        return f"TRANSBRIDGE_SECRET_{normalized.upper()}"

    def has_secret(self, name: str, context: RequestContext) -> bool:
        del context
        return bool(self._environ.get(self._key(name)))

    def get_secret(self, name: str, context: RequestContext) -> str | None:
        del context
        return self._environ.get(self._key(name)) or None


def build_headless_binding(
    entrypoint: str,
    *,
    environ: Mapping[str, str] | None = None,
    project_id: str | None = None,
    authorized_roots: tuple[str, ...] | None = None,
) -> EntrypointBinding:
    """Build one isolated runtime from safe process-level configuration."""

    environment = os.environ if environ is None else environ
    reports = (
        CapabilityReport(CapabilityId("entrypoint.capabilities"), CapabilityState.AVAILABLE),
        CapabilityReport(CapabilityId("entrypoint.project-context"), CapabilityState.AVAILABLE),
        CapabilityReport(
            CapabilityId("legacy.gui-state-tools"),
            CapabilityState.DEGRADED,
            reasons=("Headless entrypoints do not expose unsaved GUI memory.",),
        ),
    )
    ports = RuntimePorts(
        clock=SystemClock(),
        ids=UuidGenerator(),
        secrets=EnvironmentSecretStore(environment),
        security=DenyByDefaultSecurity(),
    )
    runtime = build_runtime(
        {
            "entrypoint": entrypoint,
            "task_shutdown_grace_seconds": 1.0,
        },
        capabilities=reports,
        ports=ports,
    )
    roots = authorized_roots
    if roots is None:
        roots = tuple(value for value in environment.get("TRANSBRIDGE_AUTHORIZED_ROOTS", "").split(os.pathsep) if value)
    permissions = frozenset(
        value.strip() for value in environment.get("TRANSBRIDGE_PERMISSIONS", "").split(",") if value.strip()
    )
    return bind_runtime(
        runtime,
        f"{entrypoint}:stdio" if entrypoint == "mcp" else entrypoint,
        project_id=project_id or environment.get("TRANSBRIDGE_PROJECT_ID") or None,
        variant_id=environment.get("TRANSBRIDGE_VARIANT_ID") or None,
        session_id=environment.get("TRANSBRIDGE_SESSION_ID") or None,
        permissions=permissions,
        authorized_roots=roots,
        metadata=(("entrypoint", entrypoint),),
    )
