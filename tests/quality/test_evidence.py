from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import uuid

import pytest

from transbridge.quality import (
    EvidenceValidationError,
    capture_allowed_environment,
    replay_manifest,
    run_with_evidence,
    validate_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repository_workdir():
    directory = REPOSITORY_ROOT / ".tmp_tests" / f"evidence_{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _run(output: Path, *command: str, artifact_paths: tuple[Path, ...] = ()):
    return run_with_evidence(
        command,
        repository_root=REPOSITORY_ROOT,
        output_root=output,
        artifact_paths=artifact_paths,
        require_project_python=False,
    )


def test_manifest_schema_and_hashes_validate(repository_workdir):
    outcome = _run(repository_workdir / "runs", sys.executable, "-c", "print('ok')")

    manifest = validate_manifest(outcome.manifest_path, repository_root=REPOSITORY_ROOT)

    assert outcome.return_code == 0
    assert outcome.verdict == "passed"
    assert manifest["manifest_id"] == "transbridge.qa.evidence"
    assert manifest["schema_version"] == 1
    assert manifest["command"]["process_exit_code"] == 0
    assert {item["path"] for item in manifest["inputs"]} == {"pyproject.toml", "uv.lock"}


def test_failure_exit_code_is_authoritative(repository_workdir):
    outcome = _run(repository_workdir / "runs", sys.executable, "-c", "raise SystemExit(7)")
    manifest = validate_manifest(outcome.manifest_path, repository_root=REPOSITORY_ROOT)

    assert outcome.return_code == 7
    assert outcome.verdict == "failed"
    assert manifest["command"]["verdict"] == "failed"

    manifest["command"]["verdict"] = "passed"
    outcome.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvidenceValidationError, match="verdict_does_not_match_exit_code"):
        validate_manifest(outcome.manifest_path, repository_root=REPOSITORY_ROOT)


def test_environment_allowlist_never_captures_secrets():
    secret = "canary-super-secret-value"
    captured = capture_allowed_environment(
        {"CI": "true", "OPENAI_API_KEY": secret, "UNLISTED": secret},
        {"CI", "OPENAI_API_KEY"},
    )

    assert captured == {"CI": "true"}
    assert "OPENAI_API_KEY" not in captured


def test_secret_command_argument_is_redacted_and_blocked(repository_workdir):
    secret = "canary-super-secret-value"
    marker = repository_workdir / "should-not-run"
    outcome = _run(
        repository_workdir / "runs",
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(marker)!r}).touch()",
        "--token",
        secret,
    )
    manifest_text = outcome.manifest_path.read_text(encoding="utf-8")
    manifest = validate_manifest(outcome.manifest_path, repository_root=REPOSITORY_ROOT)

    assert outcome.verdict == "blocked"
    assert outcome.return_code == 2
    assert "secret_in_command" in manifest["evidence_errors"]
    assert secret not in manifest_text
    assert not marker.exists()


def test_artifact_hash_detects_corruption(repository_workdir):
    artifact = repository_workdir / "result.xml"
    artifact.write_text("<testsuite failures='0'/>", encoding="utf-8")
    outcome = _run(repository_workdir / "runs", sys.executable, "-c", "pass", artifact_paths=(artifact,))
    manifest = validate_manifest(outcome.manifest_path, repository_root=REPOSITORY_ROOT)
    snapshot = outcome.manifest_path.parent / manifest["artifacts"][0]["snapshot_path"]
    snapshot.write_text("corrupt", encoding="utf-8")

    with pytest.raises(EvidenceValidationError, match="artifact_(hash|size)_mismatch"):
        validate_manifest(outcome.manifest_path, repository_root=REPOSITORY_ROOT)


def test_missing_lock_blocks_without_running_command(repository_workdir):
    incomplete_repository = repository_workdir / "incomplete-repository"
    incomplete_repository.mkdir()
    (incomplete_repository / "pyproject.toml").write_text("[project]\nname='probe'\n", encoding="utf-8")
    marker = incomplete_repository / "should-not-exist"
    outcome = run_with_evidence(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        repository_root=incomplete_repository,
        output_root=incomplete_repository / "runs",
        require_project_python=False,
        require_git=False,
    )
    manifest = validate_manifest(outcome.manifest_path)

    assert outcome.return_code == 2
    assert outcome.verdict == "blocked"
    assert manifest["command"]["process_exit_code"] is None
    assert "input_missing:uv.lock" in manifest["evidence_errors"]
    assert not marker.exists()


def test_non_project_python_is_an_explicit_blocker(repository_workdir):
    isolated_root = repository_workdir / "isolated"
    isolated_root.mkdir()
    (isolated_root / "pyproject.toml").write_text("[project]\nname='probe'\n", encoding="utf-8")
    (isolated_root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    marker = isolated_root / "should-not-exist"
    outcome = run_with_evidence(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        repository_root=isolated_root,
        output_root=isolated_root / "runs",
        require_git=False,
    )
    manifest = validate_manifest(outcome.manifest_path)

    assert outcome.verdict == "blocked"
    assert "project_python_unavailable" in manifest["evidence_errors"]
    assert not marker.exists()


def test_replay_reuses_command_inputs_and_artifact_hash(repository_workdir):
    artifact = repository_workdir / "deterministic.txt"
    relative_artifact = artifact.relative_to(REPOSITORY_ROOT).as_posix()
    command = (
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({relative_artifact!r}).write_text('stable', encoding='utf-8')",
    )
    first = _run(repository_workdir / "first", *command, artifact_paths=(artifact,))

    replay = replay_manifest(
        first.manifest_path,
        repository_root=REPOSITORY_ROOT,
        output_root=repository_workdir / "replay",
        require_project_python=False,
    )

    assert replay.return_code == 0
    assert replay.verdict == "passed"
    validate_manifest(replay.manifest_path, repository_root=REPOSITORY_ROOT)
