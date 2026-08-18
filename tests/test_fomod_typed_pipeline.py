"""FOMOD S02 typed DAG, terminal, cancellation, and locale contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path
import shutil
import threading
import uuid
import zipfile

import pytest

from transbridge.application.contracts import (
    Diagnostic,
    OperationOutcome,
)
from transbridge.application.fomod import (
    FOMOD_STAGE_ORDER,
    ArtifactRef,
    FomodRunSpec,
    FomodStageId,
    PipelineEngine,
    StageEventType,
    StageResult,
)
from transbridge.application.fomod.runtime import (
    FomodPipelineWorkload,
    TaskRuntimeCommitGuard,
    TaskRuntimeRunGuard,
)
from transbridge.application.tasks import (
    JobCapabilities,
    JobSpec,
    JobState,
    OwnerRef,
    TaskCancelled,
    TaskRuntime,
)
from transbridge.fomod.pipeline import FomodPipeline
from transbridge.fomod.stages import PluginTranslationSummary, default_stages


@pytest.fixture
def workdir():
    base = Path(__file__).resolve().parent.parent / ".tmp_tests"
    base.mkdir(exist_ok=True)
    directory = base / f"fomod_typed_{uuid.uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("Mod/fomod/ModuleConfig.xml", "<config/>")
        output.writestr("Mod/readme.txt", "hello")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spec(workdir: Path, *, run_id: str = "run-1", required=None) -> FomodRunSpec:
    archive = workdir / f"{run_id}.zip"
    if not archive.exists():
        _archive(archive)
    return FomodRunSpec(
        run_id=run_id,
        new_archive=str(archive),
        new_archive_hash=_hash(archive),
        output_archive=str(workdir / f"{run_id}-output.zip"),
        target_locale="ja_JP",
        config_hash="config:abc",
        workspace_root=str(workdir / "workspace"),
        ai_enabled=False,
        required_stages=frozenset(FOMOD_STAGE_ORDER) if required is None else required,
    )


class EventCollector:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


def test_real_small_fomod_runs_all_nine_stages_and_publishes_verified_archive(workdir):
    collector = EventCollector()
    spec = _spec(workdir)
    report = PipelineEngine(default_stages(), event_sink=collector).run(spec)

    assert report.outcome is OperationOutcome.COMPLETED
    assert tuple(result.stage for result in report.stages) == FOMOD_STAGE_ORDER
    assert report.run_id == "run-1"
    assert report.target_locale == "ja_JP"
    assert report.config_hash == "config:abc"
    published = next(item for item in report.artifacts if item.kind == "published-archive")
    assert published.fingerprint == _hash(Path(published.location))
    assert published.attribute("target_locale") == "ja_JP"
    with zipfile.ZipFile(published.location) as output:
        assert "fomod/ModuleConfig.xml" in output.namelist()
    assert len(collector.events) == 18
    assert collector.events[0].event_type is StageEventType.STARTED
    assert collector.events[-1].event_type is StageEventType.FINISHED


class LocalePluginSpy:
    def __init__(self, captured) -> None:
        self.captured = captured

    def translate_plugins(self, new_root, old_root, *, run_id, target_locale, ai_enabled, cancellation):
        del new_root, old_root, run_id, ai_enabled, cancellation
        self.captured.append(("plugins", target_locale))
        return PluginTranslationSummary()


class LocaleXmlSpy:
    def __init__(self, captured) -> None:
        self.captured = captured

    def translate_xml(self, new_root, old_root, *, target_locale, cancellation):
        del new_root, old_root, cancellation
        self.captured.append(("xml", target_locale))
        return True


def test_target_locale_reaches_translation_xml_output_and_report(workdir):
    captured = []
    spec = replace(_spec(workdir), ai_enabled=True)
    report = PipelineEngine(
        default_stages(
            plugin_port=LocalePluginSpy(captured),
            xml_port=LocaleXmlSpy(captured),
        )
    ).run(spec)

    assert captured == [("plugins", "ja_JP"), ("xml", "ja_JP")]
    assert report.metric(FomodStageId.XML, "target_locale") == "ja_JP"
    assert report.target_locale == "ja_JP"


def test_run_spec_and_report_contracts_are_strictly_immutable(workdir):
    spec = _spec(workdir)
    with pytest.raises(FrozenInstanceError):
        spec.target_locale = "zh_CN"
    with pytest.raises(ValueError, match="safe path segment"):
        replace(spec, run_id="../escape")
    with pytest.raises(ValueError, match="target_locale"):
        replace(spec, target_locale="")
    with pytest.raises(TypeError, match="immutable tuple"):
        ArtifactRef("id", "kind", "location", attributes=[])


def test_ambiguous_roots_and_changed_input_fingerprint_block_publication(workdir):
    ambiguous = workdir / "ambiguous.zip"
    with zipfile.ZipFile(ambiguous, "w") as output:
        output.writestr("ModA/A.esp", b"a")
        output.writestr("ModB/B.esp", b"b")
    base = _spec(workdir, run_id="ambiguous")
    ambiguous_spec = replace(
        base,
        new_archive=str(ambiguous),
        new_archive_hash=_hash(ambiguous),
    )
    ambiguous_report = PipelineEngine(default_stages()).run(ambiguous_spec)

    assert ambiguous_report.outcome is OperationOutcome.FAILED
    assert ambiguous_report.stage(FomodStageId.EXTRACT).outcome is OperationOutcome.FAILED
    assert any(item.code == "FOMOD_ROOT_CONFIRMATION_REQUIRED" for item in ambiguous_report.diagnostics)
    assert not Path(ambiguous_spec.output_archive).exists()

    changed_spec = replace(_spec(workdir, run_id="changed"), new_archive_hash="sha256:stale")
    changed_report = PipelineEngine(default_stages()).run(changed_spec)
    assert changed_report.outcome is OperationOutcome.FAILED
    assert changed_report.stage(FomodStageId.DISCOVER).outcome is OperationOutcome.FAILED
    assert any(item.code == "FOMOD_INPUT_CHANGED" for item in changed_report.diagnostics)
    assert not Path(changed_spec.output_archive).exists()


def test_structured_stage_diagnostic_survives_adapter_exception_boundary(workdir):
    class AdapterError(RuntimeError):
        code = "FOMOD_ROOT_CONFIRMATION_REQUIRED"

    class AdapterStage:
        stage_id = FomodStageId.DISCOVER
        required_artifacts = ()

        def execute(self, context):
            del context
            raise AdapterError("multiple roots need confirmation")

    remaining = default_stages()[1:]
    spec = _spec(workdir, run_id="adapter-structured-error")
    report = PipelineEngine((AdapterStage(), *remaining)).run(spec)

    assert report.outcome is OperationOutcome.FAILED
    assert any(item.code == "FOMOD_ROOT_CONFIRMATION_REQUIRED" for item in report.diagnostics)
    assert not Path(spec.output_archive).exists()


class ControlledStage:
    def __init__(
        self,
        stage_id: FomodStageId,
        calls: list[FomodStageId],
        *,
        fault_at: FomodStageId | None = None,
        cancel_at: FomodStageId | None = None,
        partial_at: FomodStageId | None = None,
        published: list[str] | None = None,
    ) -> None:
        self.stage_id = stage_id
        self.required_artifacts = (
            ()
            if stage_id is FomodStageId.DISCOVER
            else (f"artifact-{FOMOD_STAGE_ORDER[FOMOD_STAGE_ORDER.index(stage_id) - 1].value}",)
        )
        self._calls = calls
        self._fault_at = fault_at
        self._cancel_at = cancel_at
        self._partial_at = partial_at
        self._published = published

    def execute(self, context):
        self._calls.append(self.stage_id)
        if self.stage_id is self._fault_at:
            raise RuntimeError(f"controlled fault at {self.stage_id.value}")
        if self.stage_id is self._cancel_at:
            raise TaskCancelled(f"controlled cancellation at {self.stage_id.value}")
        artifact = ArtifactRef(
            f"artifact-{self.stage_id.value}",
            "published-archive" if self.stage_id is FomodStageId.PUBLISH else "controlled",
            f"memory://{self.stage_id.value}",
        )
        if self.stage_id is FomodStageId.PUBLISH:
            accepted = context.commit_guard.commit(
                context.spec.run_id,
                lambda: self._published.append(context.spec.run_id) if self._published is not None else None,
            )
            if not accepted:
                raise TaskCancelled("controlled publish rejected")
        if self.stage_id is self._partial_at:
            return StageResult(
                self.stage_id,
                OperationOutcome.PARTIAL,
                artifacts=(artifact,),
                diagnostics=(Diagnostic("CONTROLLED_PARTIAL", "controlled optional failure"),),
            )
        return StageResult.completed(self.stage_id, artifacts=(artifact,))


def _controlled_stages(calls, **kwargs):
    return tuple(ControlledStage(stage, calls, **kwargs) for stage in FOMOD_STAGE_ORDER)


@pytest.mark.parametrize("fault_at", FOMOD_STAGE_ORDER)
def test_each_required_stage_fault_stops_dag_and_prevents_later_publish(workdir, fault_at):
    calls = []
    published = []
    report = PipelineEngine(_controlled_stages(calls, fault_at=fault_at, published=published)).run(
        _spec(workdir, run_id=f"fault-{fault_at.value}")
    )

    assert report.outcome is OperationOutcome.FAILED
    assert calls == list(FOMOD_STAGE_ORDER[: FOMOD_STAGE_ORDER.index(fault_at) + 1])
    assert published == []
    assert all(item.kind != "published-archive" for item in report.artifacts)


@pytest.mark.parametrize("cancel_at", FOMOD_STAGE_ORDER)
def test_each_stage_cancellation_is_terminal_and_prevents_later_publish(workdir, cancel_at):
    calls = []
    published = []
    report = PipelineEngine(_controlled_stages(calls, cancel_at=cancel_at, published=published)).run(
        _spec(workdir, run_id=f"cancel-{cancel_at.value}")
    )

    assert report.outcome is OperationOutcome.CANCELLED
    assert calls == list(FOMOD_STAGE_ORDER[: FOMOD_STAGE_ORDER.index(cancel_at) + 1])
    assert published == []
    assert all(item.kind != "published-archive" for item in report.artifacts)


def test_optional_partial_stage_continues_but_report_is_not_completed(workdir):
    calls = []
    published = []
    optional = FomodStageId.DIFF
    required = frozenset(set(FOMOD_STAGE_ORDER) - {optional})
    report = PipelineEngine(_controlled_stages(calls, partial_at=optional, published=published)).run(
        _spec(workdir, run_id="optional-partial", required=required)
    )

    assert report.outcome is OperationOutcome.PARTIAL
    assert calls == list(FOMOD_STAGE_ORDER)
    assert published == ["optional-partial"]
    assert report.stage(optional).outcome is OperationOutcome.PARTIAL


class RejectCommitGuard:
    def commit(self, run_id, mutation):
        del run_id, mutation
        return False


def test_rejected_publish_guard_never_creates_official_output(workdir):
    spec = _spec(workdir, run_id="rejected-publish")
    report = PipelineEngine(default_stages(), commit_guard=RejectCommitGuard()).run(spec)

    assert report.outcome is OperationOutcome.CANCELLED
    assert report.stage(FomodStageId.PUBLISH).outcome is OperationOutcome.CANCELLED
    assert not Path(spec.output_archive).exists()


def test_cancellation_observed_between_stages_prevents_publish(workdir):
    cancellation = threading.Event()
    calls = []
    stages = list(_controlled_stages(calls))
    original = stages[FOMOD_STAGE_ORDER.index(FomodStageId.TRANSLATE)]

    class CancelAfterStage:
        stage_id = original.stage_id
        required_artifacts = original.required_artifacts

        def execute(self, context):
            result = original.execute(context)
            cancellation.set()
            return result

    stages[FOMOD_STAGE_ORDER.index(FomodStageId.TRANSLATE)] = CancelAfterStage()
    report = PipelineEngine(stages).run(
        _spec(workdir, run_id="between-stage-cancel"),
        cancellation,
    )

    assert report.outcome is OperationOutcome.CANCELLED
    assert calls[-1] is FomodStageId.TRANSLATE
    assert all(item.kind != "published-archive" for item in report.artifacts)


class SequenceIds:
    def __init__(self) -> None:
        self.value = 0

    def new_id(self) -> str:
        self.value += 1
        return f"runtime-{self.value}"


class AdvancingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 18, tzinfo=UTC)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value


class InlineBackend:
    def start(self, run_id, target):
        del run_id
        target()

    def cancel_hint(self, run_id):
        del run_id

    def join(self, run_id, timeout=None):
        del run_id, timeout
        return True

    def close(self, timeout=None):
        del timeout
        return True


def _runtime_case(workdir, *, fault_at=None, cancel_at=None, partial_at=None, required=None):
    case_dir = workdir / uuid.uuid4().hex
    case_dir.mkdir()
    backend = InlineBackend()
    runtime = TaskRuntime(id_generator=SequenceIds(), clock=AdvancingClock(), backend=backend)
    owner = OwnerRef("fomod-owner", "test", project_id="project")
    job = JobSpec(
        "fomod-pipeline",
        "archive:new",
        "sha256:input",
        capabilities=JobCapabilities(supports_cancel=True),
    )
    ref = runtime.submit(job, owner).ref
    spec = _spec(case_dir, run_id=ref.run_id, required=required)
    calls = []
    published = []
    engine = PipelineEngine(
        _controlled_stages(
            calls,
            fault_at=fault_at,
            cancel_at=cancel_at,
            partial_at=partial_at,
            published=published,
        ),
        run_guard=TaskRuntimeRunGuard(runtime, ref, owner),
        commit_guard=TaskRuntimeCommitGuard(runtime, ref, owner),
    )
    workload = FomodPipelineWorkload(spec, engine)
    runtime.schedule(ref, owner, workload, backend=backend)
    return runtime.get(ref, owner), workload.last_report, published


def test_task_runtime_owns_mutually_exclusive_completed_failed_cancelled_states(workdir):
    completed, completed_report, published = _runtime_case(workdir)
    failed, failed_report, failed_publish = _runtime_case(workdir, fault_at=FomodStageId.TRANSLATE)
    cancelled, cancelled_report, cancelled_publish = _runtime_case(workdir, cancel_at=FomodStageId.XML)

    assert completed.state is JobState.COMPLETED
    assert completed_report.outcome is OperationOutcome.COMPLETED
    assert published == [completed.ref.run_id]
    assert failed.state is JobState.FAILED
    assert failed_report.outcome is OperationOutcome.FAILED
    assert failed_publish == []
    assert cancelled.state is JobState.CANCELLED
    assert cancelled_report.outcome is OperationOutcome.CANCELLED
    assert cancelled_publish == []


def test_task_runtime_partial_report_is_completed_with_partial_outcome(workdir):
    optional = FomodStageId.DIFF
    required = frozenset(set(FOMOD_STAGE_ORDER) - {optional})
    snapshot, report, published = _runtime_case(
        workdir,
        partial_at=optional,
        required=required,
    )

    assert snapshot.state is JobState.COMPLETED
    assert report.outcome is OperationOutcome.PARTIAL
    assert published == [snapshot.ref.run_id]


def test_task_runtime_commit_guard_rejects_cancelled_run_before_mutation():
    runtime = TaskRuntime(
        id_generator=SequenceIds(),
        clock=AdvancingClock(),
        backend=InlineBackend(),
    )
    owner = OwnerRef("owner", "test")
    ref = runtime.submit(JobSpec("fomod", "input", "hash"), owner).ref
    runtime.start(ref, owner)
    runtime.cancel(ref, owner)
    mutated = []

    accepted = TaskRuntimeCommitGuard(runtime, ref, owner).commit(
        ref.run_id,
        lambda: mutated.append(True),
    )

    assert accepted is False
    assert mutated == []


def test_legacy_facade_delegates_to_typed_pipeline_and_requires_explicit_locale(workdir):
    source = workdir / "legacy.zip"
    _archive(source)
    output = workdir / "legacy-output.zip"
    pipeline = FomodPipeline()

    with pytest.raises(ValueError, match="target_lang is required"):
        pipeline.run(str(source), str(output), ai_enabled=False)

    result = pipeline.run(
        str(source),
        str(output),
        target_lang="ja_JP",
        ai_enabled=False,
        work_dir=str(workdir / "legacy-work"),
    )
    assert result.archive_path == str(output)
    assert pipeline.last_report is not None
    assert pipeline.last_report.target_locale == "ja_JP"
    assert pipeline.last_report.outcome is OperationOutcome.COMPLETED


def test_ai_adapter_does_not_swallow_backend_exception(monkeypatch):
    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection

    class Config:
        target_lang = "ja_JP"

    class ExplodingTranslator:
        def __init__(self, config):
            del config
            raise RuntimeError("controlled LLM failure")

    monkeypatch.setattr(
        "transbridge.ai_translator.translator.AutoTranslator",
        ExplodingTranslator,
    )
    collection = TranslationEntryCollection([
        TranslationEntry(
            id="K",
            key="K",
            original="source",
            translation="",
            stage=0,
            context="TEST",
        )
    ])

    with pytest.raises(RuntimeError, match="controlled LLM failure"):
        FomodPipeline(llm_config=Config())._ai_translate(
            collection,
            Path("plugin.esp"),
            threading.Event(),
            target_locale="ja_JP",
        )
