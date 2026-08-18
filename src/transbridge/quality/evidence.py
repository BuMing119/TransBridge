"""Versioned, replayable QA evidence manifests.

The command exit code is the source of truth.  A manifest cannot turn a
failing process into a passing run by changing a summary field: validation
always derives the verdict again from the recorded exit code and evidence
checks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
from typing import Any
import uuid

MANIFEST_ID = "transbridge.qa.evidence"
SCHEMA_VERSION = 1
DEFAULT_INPUTS = ("pyproject.toml", "uv.lock")
DEFAULT_ENV_ALLOWLIST = frozenset({
    "CI",
    "GITHUB_ACTIONS",
    "LANG",
    "LC_ALL",
    "PROCESSOR_ARCHITECTURE",
    "PYTHONHASHSEED",
    "RUNNER_ARCH",
    "RUNNER_OS",
    "TZ",
})
_SENSITIVE_NAME = re.compile(
    r"(?:^|_)(?:API_?KEY|AUTH|COOKIE|CREDENTIAL|PASS(?:WORD)?|PRIVATE|SECRET|TOKEN)(?:$|_)",
    re.IGNORECASE,
)
_SENSITIVE_ARGUMENT = re.compile(
    r"^--?(?:api[-_]?key|auth|cookie|credential|pass(?:word)?|private[-_]?key|secret|token)(?:=|$)",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


class EvidenceValidationError(ValueError):
    """Raised when evidence is malformed, inconsistent, or has changed."""


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Result of an evidence-wrapped command or blocked invocation."""

    manifest_path: Path
    return_code: int
    verdict: str


def _utc_now() -> str:
    return datetime.now(UTC).strftime(_UTC_FORMAT)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_git(repository_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository_root), *args],
        check=False,
        capture_output=True,
    )


def _repository_evidence(repository_root: Path) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        head = _run_git(repository_root, "rev-parse", "HEAD")
        status = _run_git(repository_root, "status", "--porcelain=v1", "--untracked-files=all")
    except OSError:
        head = status = None
    if head is None or status is None:
        return (
            {
                "available": False,
                "name": repository_root.name,
                "head_commit": None,
                "dirty": None,
                "diff_sha256": None,
            },
            ["repository_unavailable"],
        )
    if head.returncode != 0 or status.returncode != 0:
        return (
            {
                "available": False,
                "name": repository_root.name,
                "head_commit": None,
                "dirty": None,
                "diff_sha256": None,
            },
            ["repository_unavailable"],
        )

    diff = _run_git(repository_root, "diff", "--binary", "HEAD", "--")
    untracked = _run_git(repository_root, "ls-files", "--others", "--exclude-standard", "-z")
    if diff.returncode != 0 or untracked.returncode != 0:
        errors.append("worktree_hash_unavailable")

    digest = hashlib.sha256()
    digest.update(status.stdout)
    digest.update(diff.stdout)
    for raw_name in sorted(filter(None, untracked.stdout.split(b"\0"))):
        digest.update(raw_name)
        path = repository_root / os.fsdecode(raw_name)
        if path.is_file():
            digest.update(_sha256_file(path).encode("ascii"))

    return (
        {
            "available": True,
            "name": repository_root.name,
            "head_commit": head.stdout.decode("ascii", errors="replace").strip(),
            "dirty": bool(status.stdout),
            "diff_sha256": digest.hexdigest(),
        },
        errors,
    )


def capture_allowed_environment(
    environ: Mapping[str, str] | None = None,
    allowlist: Iterable[str] = DEFAULT_ENV_ALLOWLIST,
) -> dict[str, str]:
    """Return only explicitly safe environment values.

    A sensitive-looking name is rejected even when it is accidentally placed
    in the caller's allowlist.  Secret values and arbitrary user environment
    variables therefore never enter a manifest.
    """

    source = os.environ if environ is None else environ
    result: dict[str, str] = {}
    for name in sorted(set(allowlist)):
        if _SENSITIVE_NAME.search(name):
            continue
        if name in source:
            result[name] = source[name]
    return result


def _display_python_path(repository_root: Path) -> str:
    executable = Path(sys.executable).resolve()
    try:
        return executable.relative_to(repository_root).as_posix()
    except ValueError:
        return "<external-python>"


def _is_project_python(repository_root: Path) -> bool:
    executable = Path(sys.executable).resolve()
    expected = (repository_root / ".venv").resolve()
    try:
        executable.relative_to(expected)
    except ValueError:
        return False
    return True


def _environment_evidence(
    repository_root: Path,
    allowlist: Iterable[str],
) -> dict[str, Any]:
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "executable": _display_python_path(repository_root),
            "project_environment": _is_project_python(repository_root),
        },
        "environment_allowlist": sorted(set(allowlist)),
        "environment": capture_allowed_environment(allowlist=allowlist),
    }


def _python_blockers(environment: Mapping[str, Any]) -> list[str]:
    python = environment["python"]
    blockers: list[str] = []
    if not python["project_environment"]:
        blockers.append("project_python_unavailable")
    if sys.version_info[:2] != (3, 12) or sys.version_info[:3] < (3, 12, 12):
        blockers.append("python_3_12_12_required")
    return blockers


def _input_evidence(repository_root: Path, relative_paths: Sequence[str]) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for relative in relative_paths:
        candidate = (repository_root / relative).resolve()
        try:
            normalized = candidate.relative_to(repository_root).as_posix()
        except ValueError:
            errors.append(f"input_outside_repository:{relative}")
            continue
        if not candidate.is_file():
            errors.append(f"input_missing:{normalized}")
            continue
        records.append({
            "path": normalized,
            "size": candidate.stat().st_size,
            "sha256": _sha256_file(candidate),
        })
    return records, errors


def _artifact_source(repository_root: Path, source: Path) -> tuple[Path | None, str | None]:
    resolved = source.resolve()
    try:
        relative = resolved.relative_to(repository_root).as_posix()
    except ValueError:
        return None, "artifact_outside_repository"
    return resolved, relative


def _copy_artifacts(
    repository_root: Path,
    run_directory: Path,
    artifact_paths: Sequence[Path],
) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    target_directory = run_directory / "artifacts"
    for index, source in enumerate(artifact_paths):
        resolved, source_relative = _artifact_source(repository_root, source)
        if resolved is None:
            errors.append(f"artifact_outside_repository:{source}")
            continue
        if not resolved.is_file():
            errors.append(f"artifact_missing:{source_relative}")
            continue
        target_directory.mkdir(parents=True, exist_ok=True)
        target = target_directory / f"{index:03d}-{resolved.name}"
        shutil.copyfile(resolved, target)
        records.append({
            "source_path": source_relative,
            "snapshot_path": target.relative_to(run_directory).as_posix(),
            "size": target.stat().st_size,
            "sha256": _sha256_file(target),
        })
    return records, errors


def _derived_verdict(exit_code: int | None, evidence_errors: Sequence[str]) -> str:
    if exit_code is None:
        return "blocked"
    if exit_code != 0 or evidence_errors:
        return "failed"
    return "passed"


def _sanitize_command(command: Sequence[str]) -> tuple[list[str], list[str]]:
    sanitized: list[str] = []
    errors: list[str] = []
    redact_next = False
    for argument in command:
        if redact_next:
            sanitized.append("<redacted>")
            redact_next = False
            continue
        if _SENSITIVE_ARGUMENT.match(argument):
            errors.append("secret_in_command")
            if "=" in argument:
                sanitized.append(f"{argument.split('=', 1)[0]}=<redacted>")
            else:
                sanitized.append(argument)
                redact_next = True
            continue
        sanitized.append(argument)
    return sanitized, errors


def _wrapper_return_code(exit_code: int | None, evidence_errors: Sequence[str]) -> int:
    if exit_code is None:
        return 2
    if exit_code != 0:
        return exit_code
    return 1 if evidence_errors else 0


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite evidence manifest: {path}")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _new_run_directory(output_root: Path) -> tuple[str, Path]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run_id = f"qa-{timestamp}-{uuid.uuid4().hex[:12]}"
    directory = output_root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    return run_id, directory


def run_with_evidence(
    command: Sequence[str],
    *,
    repository_root: Path,
    output_root: Path,
    input_paths: Sequence[str] = DEFAULT_INPUTS,
    artifact_paths: Sequence[Path] = (),
    env_allowlist: Iterable[str] = DEFAULT_ENV_ALLOWLIST,
    require_project_python: bool = True,
    require_git: bool = True,
    expected_artifact_hashes: Mapping[str, str] | None = None,
) -> RunOutcome:
    """Run a command and atomically persist its reproducible evidence.

    Prerequisite failures produce a ``blocked`` manifest without running the
    command.  A non-zero process code is returned unchanged.  Evidence failures
    after a successful process use wrapper code 1.
    """

    if not command:
        raise ValueError("command must not be empty")
    repository_root = repository_root.resolve()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_id, run_directory = _new_run_directory(output_root)

    repository, repository_errors = _repository_evidence(repository_root)
    environment = _environment_evidence(repository_root, env_allowlist)
    inputs, input_errors = _input_evidence(repository_root, input_paths)
    recorded_command, command_errors = _sanitize_command(command)
    prerequisites = list(input_errors) + command_errors
    if require_git:
        prerequisites.extend(repository_errors)
    if require_project_python:
        prerequisites.extend(_python_blockers(environment))

    started_at = _utc_now()
    started = time.monotonic()
    process_exit_code: int | None = None
    if prerequisites:
        (run_directory / "stdout.log").write_text("", encoding="utf-8")
        (run_directory / "stderr.log").write_text("\n".join(prerequisites) + "\n", encoding="utf-8")
    else:
        with (run_directory / "stdout.log").open("wb") as stdout, (run_directory / "stderr.log").open("wb") as stderr:
            completed = subprocess.run(
                list(command),
                cwd=repository_root,
                stdout=stdout,
                stderr=stderr,
                check=False,
            )
        process_exit_code = completed.returncode
    ended_at = _utc_now()
    duration_ms = round((time.monotonic() - started) * 1000, 3)

    artifacts, artifact_errors = _copy_artifacts(repository_root, run_directory, artifact_paths)
    for log_name in ("stdout.log", "stderr.log"):
        log_path = run_directory / log_name
        artifacts.append({
            "source_path": None,
            "snapshot_path": log_name,
            "size": log_path.stat().st_size,
            "sha256": _sha256_file(log_path),
        })

    evidence_errors = list(prerequisites) + artifact_errors
    expected = expected_artifact_hashes or {}
    actual_by_source = {record["source_path"]: record["sha256"] for record in artifacts if record["source_path"]}
    for source_path, expected_hash in expected.items():
        if actual_by_source.get(source_path) != expected_hash:
            evidence_errors.append(f"artifact_replay_mismatch:{source_path}")

    verdict = _derived_verdict(process_exit_code, evidence_errors)
    wrapper_exit_code = _wrapper_return_code(process_exit_code, evidence_errors)
    manifest = {
        "manifest_id": MANIFEST_ID,
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": started_at,
        "repository": repository,
        "environment": environment,
        "inputs": inputs,
        "command": {
            "argv": recorded_command,
            "cwd": ".",
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "process_exit_code": process_exit_code,
            "wrapper_exit_code": wrapper_exit_code,
            "verdict": verdict,
        },
        "evidence_errors": sorted(set(evidence_errors)),
        "artifacts": artifacts,
    }
    manifest_path = run_directory / "manifest.json"
    _atomic_json(manifest_path, manifest)
    return RunOutcome(manifest_path=manifest_path, return_code=wrapper_exit_code, verdict=verdict)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceValidationError(f"Cannot read evidence manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceValidationError("Manifest root must be an object")
    return value


def validate_manifest(manifest_path: Path, *, repository_root: Path | None = None) -> dict[str, Any]:
    """Validate schema, authoritative verdict, input hashes, and artifacts."""

    manifest_path = manifest_path.resolve()
    manifest = _load_manifest(manifest_path)
    errors: list[str] = []
    if manifest.get("manifest_id") != MANIFEST_ID:
        errors.append("unsupported_manifest_id")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported_schema_version")
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"].startswith("qa-"):
        errors.append("invalid_run_id")
    repository = manifest.get("repository")
    if not isinstance(repository, dict) or not isinstance(repository.get("name"), str):
        errors.append("invalid_repository_record")
    environment = manifest.get("environment")
    if not isinstance(environment, dict) or not isinstance(environment.get("python"), dict):
        errors.append("invalid_environment_record")

    command = manifest.get("command")
    evidence_errors = manifest.get("evidence_errors")
    if not isinstance(command, dict) or not isinstance(evidence_errors, list):
        errors.append("invalid_command_record")
    else:
        exit_code = command.get("process_exit_code")
        if exit_code is not None and not isinstance(exit_code, int):
            errors.append("invalid_process_exit_code")
        else:
            derived = _derived_verdict(exit_code, evidence_errors)
            wrapper_code = _wrapper_return_code(exit_code, evidence_errors)
            if command.get("verdict") != derived:
                errors.append("verdict_does_not_match_exit_code")
            if command.get("wrapper_exit_code") != wrapper_code:
                errors.append("wrapper_exit_code_mismatch")

    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        errors.append("invalid_inputs")
        inputs = []
    for record in inputs:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            errors.append("invalid_input_record")
            continue
        if not is_sha256(record.get("sha256")) or not isinstance(record.get("size"), int):
            errors.append(f"invalid_input_hash:{record['path']}")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("invalid_artifacts")
        artifacts = []
    for record in artifacts:
        if not isinstance(record, dict) or not isinstance(record.get("snapshot_path"), str):
            errors.append("invalid_artifact_record")
            continue
        if not is_sha256(record.get("sha256")) or not isinstance(record.get("size"), int):
            errors.append(f"invalid_artifact_hash:{record['snapshot_path']}")
            continue
        artifact = (manifest_path.parent / record["snapshot_path"]).resolve()
        try:
            artifact.relative_to(manifest_path.parent)
        except ValueError:
            errors.append(f"artifact_path_escape:{record['snapshot_path']}")
            continue
        if not artifact.is_file():
            errors.append(f"artifact_missing:{record['snapshot_path']}")
            continue
        if record.get("sha256") != _sha256_file(artifact):
            errors.append(f"artifact_hash_mismatch:{record['snapshot_path']}")
        if record.get("size") != artifact.stat().st_size:
            errors.append(f"artifact_size_mismatch:{record['snapshot_path']}")

    if repository_root is not None:
        root = repository_root.resolve()
        for record in inputs:
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                errors.append("invalid_input_record")
                continue
            source = (root / record["path"]).resolve()
            try:
                source.relative_to(root)
            except ValueError:
                errors.append(f"input_path_escape:{record['path']}")
                continue
            if not source.is_file():
                errors.append(f"input_missing:{record['path']}")
            elif record.get("sha256") != _sha256_file(source):
                errors.append(f"input_hash_mismatch:{record['path']}")

    if errors:
        raise EvidenceValidationError("; ".join(errors))
    return manifest


def replay_manifest(
    manifest_path: Path,
    *,
    repository_root: Path,
    output_root: Path,
    require_project_python: bool = True,
    require_git: bool = True,
) -> RunOutcome:
    """Validate an existing manifest, rerun its command, and compare artifacts."""

    original = validate_manifest(manifest_path, repository_root=repository_root)
    artifacts = [
        Path(repository_root) / record["source_path"] for record in original["artifacts"] if record.get("source_path")
    ]
    expected = {
        record["source_path"]: record["sha256"] for record in original["artifacts"] if record.get("source_path")
    }
    return run_with_evidence(
        original["command"]["argv"],
        repository_root=repository_root,
        output_root=output_root,
        input_paths=tuple(record["path"] for record in original["inputs"]),
        artifact_paths=artifacts,
        env_allowlist=original["environment"]["environment_allowlist"],
        require_project_python=require_project_python,
        require_git=require_git,
        expected_artifact_hashes=expected,
    )


def is_sha256(value: object) -> bool:
    """Return whether a value is a lowercase SHA-256 hex digest."""

    return isinstance(value, str) and bool(_SHA256.fullmatch(value))
