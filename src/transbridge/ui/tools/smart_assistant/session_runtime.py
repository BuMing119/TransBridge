"""Capture assistant task ownership and reconcile lifecycle snapshots with live work."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from transbridge.application.contracts import JobRef, RequestContext
from transbridge.application.sessions.models import ControllerSnapshot, ControllerState, SessionJobRef
from transbridge.application.tasks.models import JobState
from transbridge.smart_assistant.tools.task_control import task_scope_matches
from transbridge.smart_assistant.tools.task_manager import TaskManager


class SessionRuntimeBinding:
    def __init__(self, *, context: Callable[[], object], session_id: Callable[[], str | None], fallback_owner: str):
        self._context = context
        self._session_id = session_id
        self._fallback_owner = fallback_owner

    def request_context(self) -> RequestContext:
        current = self._context()
        if not isinstance(current, RequestContext):
            current = RequestContext(self._fallback_owner)
        return replace(current, session_id=self._session_id())

    def scope(self) -> dict:
        context = self.request_context()
        return {key: getattr(context, key) for key in ("owner_id", "session_id", "project_id", "variant_id")}

    def jobs(self) -> tuple[SessionJobRef, ...]:
        manager = TaskManager()
        scope = self.scope()
        jobs = []
        for task_id in manager.list_all():
            status = manager.get_status(task_id)
            if status.get("error") or not task_scope_matches(status, scope):
                continue
            jobs.append(
                SessionJobRef(
                    JobRef(task_id, scope["owner_id"], status["run_id"]),
                    JobState(status["status"]),
                    int(status.get("metadata", {}).get("sequence", 0)),
                )
            )
        return tuple(jobs)

    def reconcile_controller(self, snapshot: ControllerSnapshot) -> tuple[ControllerSnapshot, bool]:
        task_id = snapshot.active_task_id
        if not task_id:
            return snapshot, False
        status = TaskManager().get_status(task_id)
        if (
            status.get("error")
            or not task_scope_matches(status, self.scope())
            or not snapshot.active_run_id
            or status.get("run_id") != snapshot.active_run_id
        ):
            return replace(
                snapshot,
                state=ControllerState.IDLE,
                react_depth=0,
                recoverable=False,
                reason="task_runtime_job_unavailable",
                active_task_id=None,
                active_run_id=None,
            ), False
        if status["status"] in {"completed", "cancelled", "failed"}:
            return replace(
                snapshot,
                state=ControllerState.IDLE,
                react_depth=0,
                recoverable=False,
                reason=f"task_already_{status['status']}",
                active_task_id=None,
                active_run_id=None,
            ), False
        if snapshot.state is ControllerState.AWAITING_TASK:
            return replace(snapshot, recoverable=True, reason=None), True
        return snapshot, True
