"""FOMOD Story S05: staging build, reopen validation and atomic publication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from transbridge.application.contracts import OperationOutcome
from transbridge.application.fomod import (
    CleanupPolicy,
    FomodManifest,
    FomodPolicies,
    FomodRunSpec,
    FomodStageId,
    StageContext,
    StagingPackPublisher,
)
from transbridge.fomod.stages import PublishStage


class _Cancelled:
    def __init__(self, value: bool) -> None:
        self._value = value

    @property
    def is_cancelled(self):
        return lambda: self._value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _spec(tmp_path: Path, *, run_id: str = "run-fomod-s05") -> FomodRunSpec:
    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    return FomodRunSpec(
        run_id=run_id,
        new_archive="input.zip",
        new_archive_hash="a" * 64,
        output_archive=str(tmp_path / "output" / "translated.zip"),
        target_locale="zh_CN",
        config_hash="c" * 64,
        policies=FomodPolicies(),
        workspace_root=str(tmp_path / "work"),
        output_format="zip",
    )


def _build_dir(tmp_path: Path) -> Path:
    build = tmp_path / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "fomod").mkdir(exist_ok=True)
    (build / "fomod" / "ModuleConfig.xml").write_text("<config/>", encoding="utf-8")
    (build / "plugin.esp").write_bytes(b"\x00\x01plugin")
    return build


def _stage_context(tmp_path: Path, build: Path, spec: FomodRunSpec, commit_guard=None):
    from transbridge.application.fomod import ArtifactRef
    from transbridge.application.fomod.pipeline import DirectCommitGuard

    return StageContext(
        spec=spec,
        workspace=Path(spec.workspace),
        artifacts={
            "build_directory": ArtifactRef(
                "build_directory",
                "build-directory",
                str(build),
            )
        },
        cancellation=None,
        commit_guard=commit_guard or DirectCommitGuard(),
    )


def test_success_chain_publishes_atomically_with_manifest_and_no_staging_leak(tmp_path) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)
    publisher = StagingPackPublisher()

    result = publisher.publish(spec, str(build), commit_guard=lambda run_id, mutation: (mutation(), True)[1])

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.published is True
    target = Path(spec.output_archive)
    assert target.exists()
    assert result.artifact_sha256 == _sha256(target)
    assert result.artifact_size == target.stat().st_size

    # Reopenable and non-empty.
    from transbridge.fileops import inspect_archive

    reopened = inspect_archive(str(target))
    assert len(reopened.files) == 2

    # Manifest corresponds to input hashes, policies and run_id.
    assert result.manifest_path is not None
    manifest = FomodManifest.from_dict(json.loads(Path(result.manifest_path).read_text(encoding="utf-8")))
    assert manifest.run_id == "run-fomod-s05"
    assert manifest.target_locale == "zh_CN"
    assert manifest.new_archive_hash == "a" * 64
    assert manifest.config_hash == "c" * 64
    assert manifest.policy_ids == FomodPolicies().as_tuple()
    assert manifest.artifact_sha256 == result.artifact_sha256

    # No staging leftovers beside the target.
    leftovers = [p for p in target.parent.iterdir() if ".stage" in p.name]
    assert leftovers == []


def test_pack_failure_preserves_old_target_and_cleans_staging(tmp_path) -> None:
    spec = _spec(tmp_path)
    target = Path(spec.output_archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"old-archive-bytes")
    old_hash = _sha256(target)
    publisher = StagingPackPublisher()

    result = publisher.publish(spec, str(tmp_path / "missing-build-dir"))

    assert result.outcome is OperationOutcome.FAILED
    assert result.code in {"FOMOD_PACK_FAILED", "FOMOD_ARCHIVE_EMPTY", "FOMOD_ARCHIVE_REOPEN_FAILED"}
    assert result.published is False
    assert _sha256(target) == old_hash
    leftovers = [p for p in target.parent.iterdir() if ".stage" in p.name]
    assert leftovers == []


def test_cancel_before_commit_leaves_target_untouched(tmp_path) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)
    target = Path(spec.output_archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"existing-target")
    old_hash = _sha256(target)

    result = StagingPackPublisher().publish(
        spec,
        str(build),
        cancellation=_Cancelled(True),
        expected_target_sha256=old_hash,
    )

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.published is False
    assert _sha256(target) == old_hash


def test_target_fingerprint_conflict_fails_without_touching_target(tmp_path) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)
    target = Path(spec.output_archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original")
    original = _sha256(target)

    result = StagingPackPublisher().publish(
        spec, str(build), expected_target_sha256="f" * 64, cleanup_policy=CleanupPolicy.CLEAN_ALWAYS
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.code == "TARGET_FINGERPRINT_CONFLICT"
    assert _sha256(target) == original


def test_commit_guard_rejection_never_creates_official_output(tmp_path) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)

    result = StagingPackPublisher().publish(spec, str(build), commit_guard=lambda run_id, mutation: False)

    assert result.outcome is OperationOutcome.CANCELLED
    assert result.code == "PUBLISH_COMMIT_REJECTED"
    assert not Path(spec.output_archive).exists()


def test_retain_on_failure_keeps_staging_for_debug(tmp_path) -> None:
    spec = _spec(tmp_path)

    result = StagingPackPublisher().publish(
        spec,
        str(tmp_path / "missing"),
        cleanup_policy=CleanupPolicy.RETAIN_ON_FAILURE,
    )

    assert result.outcome is OperationOutcome.FAILED
    assert result.published is False
    assert result.staged_path is not None
    assert Path(result.staged_path).exists()
    leftovers = [p for p in Path(spec.output_archive).parent.iterdir() if ".stage" in p.name]
    assert len(leftovers) == 1


def test_success_consumes_staging_atomically(tmp_path) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)
    result = StagingPackPublisher().publish(spec, str(build), cleanup_policy=CleanupPolicy.RETAIN_ALWAYS)
    assert result.outcome is OperationOutcome.COMPLETED
    assert Path(spec.output_archive).exists()
    # The staging file was atomically replaced into the official target, so the
    # staged path no longer exists and nothing is left beside the output.
    assert result.staged_path is None
    leftovers = [p for p in Path(spec.output_archive).parent.iterdir() if ".stage" in p.name]
    assert leftovers == []


def test_publish_stage_maps_publisher_outcome_to_typed_stage_result(tmp_path) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)
    stage = PublishStage()
    context = _stage_context(tmp_path, build, spec)

    result = stage.execute(context)

    assert result.stage is FomodStageId.PUBLISH
    assert result.outcome is OperationOutcome.COMPLETED
    ids = {artifact.artifact_id for artifact in result.artifacts}
    assert "published_archive" in ids
    assert "publish_manifest" in ids
    assert Path(spec.output_archive).exists()


def test_manifest_failure_after_replace_is_truthful_partial_with_published_artifact(tmp_path, monkeypatch) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)
    target = Path(spec.output_archive)
    target.write_bytes(b"previous archive")
    previous_hash = _sha256(target)
    publisher = StagingPackPublisher()

    def fail_manifest(*_args, **_kwargs):
        raise OSError("manifest disk fault")

    monkeypatch.setattr(publisher, "_write_manifest", fail_manifest)
    result = publisher.publish(spec, str(build))

    assert result.outcome is OperationOutcome.PARTIAL
    assert result.code == "PUBLISH_MANIFEST_FAILED"
    assert result.published is True
    assert result.artifact_sha256 == _sha256(target)
    assert result.artifact_sha256 != previous_hash
    assert result.backup_path is not None
    assert _sha256(Path(result.backup_path)) == previous_hash
    assert result.manifest_path is None
    assert any(item.code == "PUBLISH_MANIFEST_FAILED" for item in result.diagnostics)


def test_publish_stage_exposes_committed_archive_when_manifest_evidence_is_partial(tmp_path, monkeypatch) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)
    publisher = StagingPackPublisher()
    monkeypatch.setattr(
        publisher,
        "_write_manifest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("manifest fault")),
    )

    result = PublishStage(publisher).execute(_stage_context(tmp_path, build, spec))

    assert result.outcome is OperationOutcome.PARTIAL
    assert {artifact.artifact_id for artifact in result.artifacts} == {"published_archive"}
    assert any(item.code == "PUBLISH_MANIFEST_FAILED" for item in result.diagnostics)
    assert Path(spec.output_archive).exists()


def test_publish_stage_rejected_guard_is_cancelled_without_output(tmp_path) -> None:
    spec = _spec(tmp_path)
    build = _build_dir(tmp_path)

    class RejectingGuard:
        def commit(self, run_id, mutation):
            return False

    stage = PublishStage()
    context = _stage_context(tmp_path, build, spec, commit_guard=RejectingGuard())

    result = stage.execute(context)

    assert result.outcome is OperationOutcome.CANCELLED
    assert not Path(spec.output_archive).exists()


def test_fomod_manifest_round_trips_without_loss() -> None:
    manifest = FomodManifest.from_spec(
        _spec(Path(".")),
        build_fingerprint="b" * 64,
        artifact_sha256="d" * 64,
        artifact_size=123,
    )
    restored = FomodManifest.from_dict(json.loads(json.dumps(manifest.to_dict())))
    assert restored == manifest
