from __future__ import annotations

from datetime import UTC, datetime

from transbridge.application.tasks import CallbackThreadBackend, TaskRuntime
from transbridge.bootstrap import build_runtime


class Ids:
    def new_id(self) -> str:
        return "runtime-id"


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 18, tzinfo=UTC)


def test_composition_root_injects_task_runtime_and_releases_it_before_resources():
    calls = []
    backend = CallbackThreadBackend(
        dispatch=lambda _run_id, _target: None,
        release=lambda _timeout: calls.append("tasks") or True,
    )
    tasks = TaskRuntime(id_generator=Ids(), clock=Clock(), backend=backend)

    class Resource:
        def close(self) -> None:
            calls.append("resource")

    app = build_runtime(task_runtime=tasks, resources=(Resource(),))
    assert app.tasks is tasks

    app.close()

    assert calls == ["tasks", "resource"]
