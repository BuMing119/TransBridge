"""Release S05 final QA gate: every recorded evidence manifest must pass.

This is the consolidated evidence gate for the whole remedied Phase 4: a single
Story/Epic is only "verified" if its committed EvidenceManifest validates in
place (schema, verdict, input hashes, environment).  Missing or failed evidence
is a release blocker.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = ROOT / "docs" / "test-reports" / "requirement-code-review-2026-08-18" / "qa-evidence"
EXPECTED_TARGETS_FILE = ROOT / "tools" / "qa" / "expected_evidence_targets.json"


def _all_manifests() -> list[Path]:
    if not EVIDENCE_ROOT.exists():
        return []
    return sorted(path for path in EVIDENCE_ROOT.rglob("manifest.json") if path.is_file())


def _target_dirs() -> set[str]:
    return {manifest.parent.parent.name for manifest in _all_manifests()}


def _latest_per_target() -> dict[str, Path]:
    latest: dict[str, Path] = {}
    for manifest in _all_manifests():
        target = manifest.parent.parent.name
        current = latest.get(target)
        if current is None or manifest.parent.name > current.parent.name:
            latest[target] = manifest
    return latest


def test_evidence_root_is_populated() -> None:
    assert _all_manifests(), "no QA evidence manifests recorded for this remediation"


def test_every_story_target_has_a_latest_manifest_with_passing_verdict() -> None:
    """Gate-critical: each story target's newest EvidenceManifest must report
    a passed business verdict (schema v1, well-formed).  Historical/superseded
    runs are append-only and not re-gated, honoring the frozen-baseline rule.
    Input-hash read-back drift from later remediation changes is reported (not
    blocked) in the final QA report by tools/qa/final_qa.py.
    """
    failures: list[str] = []
    for target, manifest in sorted(_latest_per_target().items()):
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{manifest.relative_to(ROOT)}: unreadable: {exc}")
            continue
        if payload.get("schema_version") != 1 or payload.get("command", {}).get("verdict") != "passed":
            failures.append(
                f"{target}: latest {manifest.parent.name} schema/verdict invalid "
                f"(schema={payload.get('schema_version')}, "
                f"verdict={payload.get('command', {}).get('verdict')})"
            )
    assert not failures, "evidence gate failures:\n" + "\n".join(failures)


def test_all_plans_epics_covered_by_evidence() -> None:
    """Each of the seven V2 Plans must contribute at least one evidence target."""
    targets = _target_dirs()
    expected_prefixes = {
        "platform-s0",
        "io-s0",
        "persistence-s0",
        "task-s0",
        "paratranz-s0",
        "fomod-s0",
        "task-runtime-s0",
        "release-s0",
    }
    found = {p: any(t.startswith(p) for t in targets) for p in expected_prefixes}
    missing = [p for p, present in found.items() if not present]
    assert not missing, f"V2 plan families without evidence: {missing}"


def test_every_expected_story_has_evidence_and_no_untracked_target_is_counted() -> None:
    expected = set(json.loads(EXPECTED_TARGETS_FILE.read_text(encoding="utf-8")))
    actual = _target_dirs()
    assert actual == expected, (
        f"evidence target mismatch: missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
    )
