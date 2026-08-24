from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import zipfile

from transbridge.application.contracts import OperationOutcome
from transbridge.application.fomod import (
    FomodTaskDraft,
    FomodTaskEntrypoint,
    FomodTaskPreflightService,
    PipelineResult,
)
from transbridge.application.tasks import CallbackThreadBackend, JobState, OwnerRef, TaskRuntime


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
