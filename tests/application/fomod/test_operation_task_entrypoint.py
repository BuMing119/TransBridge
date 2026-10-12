from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import zipfile

import pytest

from transbridge.application.contracts import OperationOutcome
from transbridge.application.fomod import (
    FomodTaskDraft,
    FomodTaskEntrypoint,
    FomodTaskPreflightService,
    PipelineResult,
)
from transbridge.application.tasks import CallbackThreadBackend, JobEventType, JobState, OwnerRef, TaskRuntime


class Ids:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"fomod-run-{self.value}"


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 24, tzinfo=UTC)

    def now(self):
        self.value += timedelta(microseconds=1)
        return self.value


class CompletedEngine:
    def run(self, spec, cancellation):
        del cancellation
        return PipelineResult(
            spec.run_id,
            spec.target_locale,
            spec.config_hash,
            OperationOutcome.COMPLETED,
            (),
            (),
            (),
        )


def archive(path: Path, name: str = "fomod/info.xml") -> None:
    with zipfile.ZipFile(path, "w") as output:
        output.writestr(name, "<fomod />")


def test_fomod_preflight_and_task_share_runtime_run_identity(tmp_path: Path) -> None:
    source = tmp_path / "new.zip"
    archive(source)
    output = tmp_path / "translated.zip"
    draft = FomodTaskDraft(
        str(source),
        str(output),
        "zh_CN",
        hashlib.sha256(b"config").hexdigest(),
        workspace_root=str(tmp_path / "work"),
    )
    checked = FomodTaskPreflightService().preflight(draft)
    assert checked.ready

    captured = []
    runtime = TaskRuntime(
        id_generator=Ids(),
        clock=Clock(),
        backend=CallbackThreadBackend(lambda _run_id, target: target()),
    )

    def engine_factory(spec, run_guard, commit_guard):
        captured.append((spec, run_guard, commit_guard))
        return CompletedEngine()

    entrypoint = FomodTaskEntrypoint(runtime, engine_factory)
    owner = OwnerRef("gui", "fomod", session_id="s")
    ref = entrypoint.submit(checked, owner)

    assert captured[0][0].run_id == ref.run_id
    assert runtime.get(ref, owner).state is JobState.COMPLETED
    assert entrypoint.report(ref, owner).run_id == ref.run_id


def test_fomod_preflight_rejects_traversal_and_unconfirmed_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "attack.zip"
    archive(source, "../escape.txt")
    output = tmp_path / "translated.zip"
    output.write_bytes(b"old")
    checked = FomodTaskPreflightService().preflight(
        FomodTaskDraft(
            str(source),
            str(output),
            "zh_CN",
            hashlib.sha256(b"config").hexdigest(),
        )
    )

    assert not checked.ready
    assert "FOMOD_OVERWRITE_CONFIRMATION_REQUIRED" in checked.diagnostics
    assert "FOMOD_NEW_ARCHIVE_POLICY_REJECTED" in checked.diagnostics


@pytest.fixture
def setup_run(tmp_path):
    source = tmp_path / "new.zip"
    archive(source)
    checked = FomodTaskPreflightService().preflight(
        FomodTaskDraft(str(source), str(tmp_path / "out.zip"), "zh_CN", "config")
    )
    assert checked.ready
    events = []
    pending = []
    runtime = TaskRuntime(
        id_generator=Ids(), clock=Clock(), backend=CallbackThreadBackend(lambda _id, target: pending.append(target))
    )
    subscription = runtime.subscribe(events.append)
    owner = OwnerRef("gui", "fomod")
    try:
        yield checked, runtime, owner, pending, events
    finally:
        for task in runtime.list(owner):
            if not task.is_terminal:
                runtime.cancel(task.ref, owner)
        while pending:
            pending.pop(0)()
        subscription.close()
        runtime.close()


@pytest.mark.parametrize("failure_at", ["workspace", "spec", "engine"])
def test_initialization_failures_are_runtime_owned_and_clean_empty_workspace(
    setup_run, tmp_path, monkeypatch, failure_at
):
    from transbridge.application.fomod import runtime as module

    checked, runtime, owner, pending, events = setup_run
    workspace = tmp_path / "owned-workspace"

    def allocate_workspace(**kwargs):
        if failure_at == "workspace":
            raise OSError("controlled workspace failure")
        workspace.mkdir()
        return str(workspace)

    def broken_spec(**kwargs):
        raise ValueError("controlled spec failure")

    def engine_factory(*args):
        raise OSError("controlled engine failure")

    monkeypatch.setattr(module.tempfile, "mkdtemp", allocate_workspace)
    if failure_at == "spec":
        monkeypatch.setattr(module, "FomodRunSpec", broken_spec)
    entrypoint = FomodTaskEntrypoint(runtime, engine_factory)
    ref = entrypoint.submit(checked, owner)
    assert runtime.get(ref, owner).state is JobState.RUNNING
    assert not workspace.exists(), "initialization must run in the scheduled workload"
    pending.pop(0)()

    assert runtime.get(ref, owner).state is JobState.FAILED
    assert [e.snapshot.state for e in events if e.event_type is JobEventType.FINISHED] == [JobState.FAILED]
    assert any(e.code == "backend_workload_failed" and f"controlled {failure_at}" in e.message for e in events)
    assert not workspace.exists()
    assert not Path(checked.draft.output_archive).exists()
    assert entrypoint.report(ref, owner) is None


@pytest.mark.parametrize("cancel_at", ["before_setup", "during_setup"])
def test_initialization_cancellation_never_runs_pipeline(setup_run, tmp_path, monkeypatch, cancel_at):
    from transbridge.application.fomod import runtime as module

    checked, runtime, owner, pending, events = setup_run
    workspace = tmp_path / "owned-workspace"
    calls = []

    def allocate_workspace(**kwargs):
        calls.append("workspace")
        workspace.mkdir()
        return str(workspace)

    class Engine:
        def run(self, spec, cancellation):
            pytest.fail("a cancelled initialization must not run the pipeline")

    def engine_factory(*args):
        calls.append("engine")
        runtime.cancel(ref, owner)
        return Engine()

    monkeypatch.setattr(module.tempfile, "mkdtemp", allocate_workspace)
    entrypoint = FomodTaskEntrypoint(runtime, engine_factory)
    ref = entrypoint.submit(checked, owner)
    if cancel_at == "before_setup":
        runtime.cancel(ref, owner)
    pending.pop(0)()

    assert runtime.get(ref, owner).state is JobState.CANCELLED
    assert calls == ([] if cancel_at == "before_setup" else ["workspace", "engine"])
    assert not workspace.exists()
    assert [e.snapshot.state for e in events if e.event_type is JobEventType.FINISHED] == [JobState.CANCELLED]


def test_backend_start_failure_has_one_terminal_and_no_initialization(setup_run):
    checked, runtime, owner, _pending, events = setup_run

    def fail_start(_run_id, _target):
        raise RuntimeError("controlled backend failure")

    runtime._backend = CallbackThreadBackend(fail_start)
    entrypoint = FomodTaskEntrypoint(runtime, lambda *_args: pytest.fail("setup must not run"))
    with pytest.raises(RuntimeError, match="controlled backend failure"):
        entrypoint.submit(checked, owner)
    assert [task.state for task in runtime.list(owner)] == [JobState.FAILED]
    assert len([e for e in events if e.event_type is JobEventType.FINISHED]) == 1


def test_cancelled_initialization_with_unremovable_workspace_blocks_close(setup_run, tmp_path, monkeypatch):
    from transbridge.application.fomod import runtime as module

    checked, runtime, owner, pending, events = setup_run
    workspace = tmp_path / "retained-workspace"

    def allocate_workspace(**_kwargs):
        workspace.mkdir()
        (workspace / "evidence").write_text("preserve", encoding="utf-8")
        return str(workspace)

    def engine_factory(*_args):
        runtime.cancel(ref, owner)
        return CompletedEngine()

    monkeypatch.setattr(module.tempfile, "mkdtemp", allocate_workspace)
    ref = FomodTaskEntrypoint(runtime, engine_factory).submit(checked, owner)
    pending.pop(0)()
    assert any(event.code == "task_cleanup_failed" for event in events)
    drain = runtime.prepare_close()
    assert "workspace retained" in drain.poll().errors[0]
    drain.close()
    assert (workspace / "evidence").read_text(encoding="utf-8") == "preserve"
