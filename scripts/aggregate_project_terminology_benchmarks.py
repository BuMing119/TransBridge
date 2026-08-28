"""Aggregate complete FR5.16 regular/stress evidence into one release-gate bundle."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from transbridge.application.terminology.benchmark_gates import write_benchmark_bundle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regular-dir", type=Path, required=True)
    parser.add_argument("--stress-dir", type=Path, required=True)
    parser.add_argument("--supplemental-evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        artifact = write_benchmark_bundle(
            args.regular_dir,
            args.stress_dir,
            args.supplemental_evidence,
            args.output,
            overwrite=args.overwrite,
        )
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"wrote {artifact.path}")
    print(f"artifact digest: {artifact.artifact_digest}")
    print(f"FR5.16 SHALL budgets passed: {str(artifact.result.shall_passed).lower()}")
    print(f"FR5.16 SHOULD budgets passed: {str(artifact.result.should_passed).lower()}")
    for check in artifact.result.evidence.checks:
        print(f"release check {check.check_id}: {check.status.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
