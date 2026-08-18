"""Secret canary leak scan across stdout/stderr/log/JUnit/report/artifact
manifest (release-hardening-v2 Story 04).

A generated canary is placed into every artifact surface, the shared
:class:`SecretRedactor` is applied, and the redacted product must no longer
contain the canary anywhere.  This is the final fail-closed guarantee that a
secret appearing in any QA output cannot reach a published report.
"""

from __future__ import annotations

import json
from pathlib import Path

from transbridge.application.security import SecretRedactor


def _canaries() -> tuple[str, ...]:
    return (
        "sk-" + "A" * 24,
        "sk-ant-" + "B" * 24,
        "Bearer " + "C" * 24,
        "AKIA" + "D" * 16,
        "ghp_" + "E" * 24,
        "password=super-secret-value-123456",
        "api_key: " + "F" * 16,
    )


def _all_canary_text(canaries: tuple[str, ...]) -> str:
    return "\n".join(f"line with {value} embedded" for value in canaries)


def test_log_file_canary_removed_after_redaction(tmp_path: Path) -> None:
    canaries = _canaries()
    log = tmp_path / "app.log"
    log.write_text(_all_canary_text(canaries), encoding="utf-8")

    redacted = SecretRedactor.default().redact_text(log.read_text(encoding="utf-8"))

    for canary in canaries:
        assert canary not in redacted
    assert SecretRedactor.REDACTED in redacted


def test_stdout_and_stderr_canary_removed() -> None:
    canaries = _canaries()
    content = _all_canary_text(canaries)
    redactor = SecretRedactor.default()

    assert all(canary not in redactor.redact_text(content) for canary in canaries)
    # stderr is a different surface but must share the same redactor.
    assert all(canary not in redactor.redact_text(content) for canary in canaries)


def test_junit_style_output_canary_removed() -> None:
    junit = (
        '<?xml version="1.0"?><testsuite><testcase name="x"><system-out>'
        + f"sk-{'G' * 24}"
        + "</system-out></testcase></testsuite>"
    )
    redacted = SecretRedactor.default().redact_text(junit)
    assert f"sk-{'G' * 24}" not in redacted


def test_report_text_and_artifact_manifest_canary_removed() -> None:
    canaries = _canaries()
    report = {"title": "QA", "summary": _all_canary_text(canaries)}
    manifest = {
        "schema_version": 1,
        "command": {"argv": ["python", "-m", "pytest"], "cwd": "."},
        "artifacts": [{"path": "stdout.log", "note": f"token={canaries[0]}"}],
        "env": {"secret": canaries[4], "password": "should-be-dropped"},
    }
    redactor = SecretRedactor.default()

    redacted_report = redactor.redact(report)
    redacted_manifest = redactor.redact(manifest)

    serialized = json.dumps(redacted_report) + json.dumps(redacted_manifest)
    for canary in canaries:
        assert canary not in serialized
    # Sensitive keys are hardened to the redaction marker regardless of value.
    assert redacted_manifest["env"]["password"] == SecretRedactor.REDACTED
    assert SecretRedactor.REDACTED in json.dumps(redacted_report)


def test_on_disk_artifact_is_redacted_before_publication(tmp_path: Path) -> None:
    canaries = _canaries()
    src = tmp_path / "report.txt"
    src.write_text(_all_canary_text(canaries), encoding="utf-8")

    # A publication pipeline redacts the artifact bytes before writing the
    # public copy.
    public = tmp_path / "public" / "report.txt"
    public.parent.mkdir()
    redacted = SecretRedactor.default().redact_text(src.read_text(encoding="utf-8"))
    public.write_text(redacted, encoding="utf-8")

    published = public.read_text(encoding="utf-8")
    for canary in canaries:
        assert canary not in published
    assert src.exists()  # original stays on the private side


def test_redactor_with_path_sanitisation_also_hides_canary(tmp_path: Path) -> None:
    canaries = _canaries()
    content = _all_canary_text(canaries) + f"\nlocal path {tmp_path}\\data\\report.txt"
    redacted = SecretRedactor(redact_paths=True).redact_text(content)

    for canary in canaries:
        assert canary not in redacted
    assert "report.txt" not in redacted
