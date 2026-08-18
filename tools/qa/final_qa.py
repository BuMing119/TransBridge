"""Generate the consolidated Phase 4 final QA report from evidence manifests."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = ROOT / "docs" / "test-reports" / "requirement-code-review-2026-08-18" / "qa-evidence"
OUT = ROOT / "docs" / "test-reports" / "requirement-code-review-2026-08-18" / "final-release-qa-2026-08-18.md"
EXPECTED_TARGETS_FILE = ROOT / "tools" / "qa" / "expected_evidence_targets.json"


def _load(manifest: Path) -> dict:
    return json.loads(manifest.read_text(encoding="utf-8"))


def _latest_per_target(manifests: list[Path]) -> dict[str, Path]:
    latest: dict[str, Path] = {}
    for manifest in manifests:
        target = manifest.parent.parent.name
        current = latest.get(target)
        if current is None or manifest.parent.name > current.parent.name:
            latest[target] = manifest
    return latest


def _read_back_drift(manifest: Path, payload: dict) -> list[str]:
    """Compare recorded input hashes against current files (informational)."""
    drift: list[str] = []
    for record in payload.get("inputs", ()):
        current = Path(str(ROOT / record["path"]))
        if not current.is_file():
            drift.append(f"{record['path']}: missing")
            continue
        digest = _sha256(current)
        if digest != record["sha256"]:
            drift.append(f"{record['path']}: hash changed")
    return drift


def _sha256(file: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with file.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    manifests = sorted(EVIDENCE_ROOT.rglob("manifest.json"))
    if not manifests:
        print("no evidence manifests", file=sys.stderr)
        return 1
    latest = _latest_per_target(manifests)
    expected = set(json.loads(EXPECTED_TARGETS_FILE.read_text(encoding="utf-8")))
    actual = set(latest)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    invalid = []
    for target, manifest in sorted(latest.items()):
        payload = _load(manifest)
        if payload.get("schema_version") != 1 or payload.get("command", {}).get("verdict") != "passed":
            invalid.append(target)
    if missing or unexpected or invalid:
        print(
            f"evidence target gate failed: missing={missing}, unexpected={unexpected}, invalid={invalid}",
            file=sys.stderr,
        )
        return 1

    lines: list[str] = []
    lines.append("# TransBridge 综合整改 —— 最终 QA 汇总（Phase 4 evidence 门禁）")
    lines.append("")
    lines.append("- 日期：2026-08-18")
    lines.append("- 来源：`qa-evidence/` 下每 Story 目标的最新 EvidenceManifest（schema v1，业务 verdict）")
    lines.append("- 门禁：`tests/packaging/test_final_qa_gate.py` 断言每目标最新 evidence `passed` 且 schema 有效")
    lines.append("")
    lines.append("| Story 目标 | 最新 run_id | 最终 verdict | input 回读 |")
    lines.append("|---|---|---|---|")
    drift_all = 0
    for target in sorted(latest):
        manifest = latest[target]
        payload = _load(manifest)
        verdict = payload["command"]["verdict"]
        drift = _read_back_drift(manifest, payload)
        drift_label = "clean" if not drift else f"drift({len(drift)})"
        drift_all += len(drift)
        lines.append(f"| {target} | `{payload['run_id']}` | {verdict} | {drift_label} |")
    lines.append("")
    lines.append(f"- 共 {len(latest)}/{len(expected)} 个预期 Story 目标；业务 verdict 全部为 passed。")
    if drift_all:
        lines.append(
            f"- input 回读漂移合计 {drift_all} 项：因整改全程依赖/代码被后续 Story 演进，冻结 Story 的旧 evidence "
            "记录相应文件当时的 hash；按冻结基线不重做，漂移不作为 blocker（记录于此）。"
        )
    else:
        lines.append("- input 回读全部 clean。")
    lines.append("")
    lines.append("- 正式审查报告回填（R-001～R-050）见 remediation-ledger 覆盖矩阵与各 Story 记录门禁。")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
