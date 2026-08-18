"""Read-only JobSnapshot projection for GUI, Agent and MCP entrypoints.

The monitor widget, agent tools and MCP handlers all render the same public
view produced from one immutable :class:`JobSnapshot`; no entrypoint may mutate
job state directly.  Controls go through the runtime with an explicit actor
scope, and buttons are enabled from a capability projection.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Protocol

from transbridge.application.contracts import JobRef

from .controls import ControlProjection
from .models import JobSnapshot, OwnerRef, TaskAccessError, TransitionError
from .runtime import TaskRuntime

#: Capability-controlled actions understood by monitor buttons.
MONITOR_ACTIONS = frozenset({"pause", "resume", "cancel", "cleanup", "cleanup_completed"})


def job_snapshot_to_view(snapshot: JobSnapshot) -> dict[str, Any]:
    """Canonical public projection consumed by GUI, Agent and MCP alike.

    The legacy TaskMonitorWidget keys (task_id/status/progress/metadata/
    created_at) are preserved so the existing card renderer keeps working
    while the authoritative source becomes the immutable snapshot.
    """
    capabilities = snapshot.specification.capabilities
    metadata = {
        "type": snapshot.specification.job_type,
        "name": snapshot.specification.display_name or snapshot.specification.job_type,
        "run_id": snapshot.ref.run_id,
        "revision": snapshot.revision,
        "sequence": snapshot.sequence,
        "owner_id": snapshot.owner.owner_id,
        "entrypoint": snapshot.owner.entrypoint,
        "project_id": snapshot.owner.project_id,
        "session_id": snapshot.owner.session_id,
        "config_digest": snapshot.specification.config_digest,
        "supports_pause": capabilities.supports_pause,
        "supports_resume": capabilities.supports_resume,
        "supports_cancel": capabilities.supports_cancel,
        "supports_checkpoint": capabilities.supports_checkpoint,
    }
    metadata.update(dict(snapshot.specification.metadata))
    return {
        "task_id": snapshot.ref.job_id,
        "job_id": snapshot.ref.job_id,
        "run_id": snapshot.ref.run_id,
        "status": snapshot.state.value,
        "state": snapshot.state.value,
        "progress": dict(snapshot.progress),
        "metadata": metadata,
        "created_at": snapshot.created_at.timestamp(),
        "updated_at": snapshot.updated_at.timestamp(),
        "is_terminal": snapshot.is_terminal,
        "revision": snapshot.revision,
        "owner": {
            "owner_id": snapshot.owner.owner_id,
            "entrypoint": snapshot.owner.entrypoint,
            "project_id": snapshot.owner.project_id,
            "variant_id": snapshot.owner.variant_id,
            "session_id": snapshot.owner.session_id,
        },
        "capabilities": {
            "pause": capabilities.supports_pause,
            "resume": capabilities.supports_resume,
            "cancel": capabilities.supports_cancel,
            "checkpoint": capabilities.supports_checkpoint,
        },
    }


def job_snapshot_to_public_dict(snapshot: JobSnapshot) -> dict[str, Any]:
    """JSON-safe schema used by Agent tools and MCP responses (same projection)."""
    return job_snapshot_to_view(snapshot)


class TaskProjectionPort(Protocol):
    def list(self, actor: OwnerRef) -> tuple[JobSnapshot, ...]: ...

    def get(self, ref: JobRef, actor: OwnerRef) -> JobSnapshot: ...

    def controls(self, ref: JobRef, actor: OwnerRef) -> ControlProjection: ...

    def control(self, ref: JobRef, actor: OwnerRef, action: str) -> JobSnapshot: ...


@dataclass(frozen=True, slots=True)
class ControlActionResult:
    snapshot: JobSnapshot | None
    action: str
    accepted: bool
    code: str = ""
    message: str = ""


class RuntimeTaskProjection:
    """Read-only view over TaskRuntime; the only writer is the runtime."""

    def __init__(self, runtime: TaskRuntime) -> None:
        self._runtime = runtime

    def list(self, actor: OwnerRef) -> tuple[JobSnapshot, ...]:
        return self._runtime.list(actor)

    def get(self, ref: JobRef, actor: OwnerRef) -> JobSnapshot:
        return self._runtime.get(ref, actor)

    def controls(self, ref: JobRef, actor: OwnerRef) -> ControlProjection:
        return self._runtime.controls(ref, actor)

    def control(self, ref: JobRef, actor: OwnerRef, action: str) -> ControlActionResult:
        """Route a monitor button to the runtime; cleanup is view-local only."""
        if action not in MONITOR_ACTIONS:
            return ControlActionResult(None, action, False, "unknown_action", "unsupported monitor action")
        try:
            if action == "pause":
                snapshot = self._runtime.pause(ref, actor)
            elif action == "resume":
                snapshot = self._runtime.resume(ref, actor)
            elif action == "cancel":
                snapshot = self._runtime.cancel(ref, actor)
            else:  # cleanup / cleanup_completed are display-only
                return ControlActionResult(None, action, True, "view_local", "cleanup is a view-local action")
        except TransitionError as exc:
            return ControlActionResult(None, action, False, exc.code, str(exc))
        except TaskAccessError as exc:
            return ControlActionResult(None, action, False, exc.code, str(exc))
        return ControlActionResult(snapshot, action, True)


def view_age_seconds(snapshot: JobSnapshot, *, now: float | None = None) -> float:
    """Elapsed wall time since creation; display-only helper."""
    reference = time.time() if now is None else now
    return max(0.0, reference - snapshot.created_at.timestamp())
