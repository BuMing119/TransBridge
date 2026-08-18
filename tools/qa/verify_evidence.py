"""Verify a QA evidence manifest and all recorded hashes."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from transbridge.quality import EvidenceValidationError, validate_manifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = validate_manifest(args.manifest, repository_root=REPOSITORY_ROOT)
    except EvidenceValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"VALID: {manifest['run_id']} ({manifest['command']['verdict']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
