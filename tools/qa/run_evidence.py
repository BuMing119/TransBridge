"""Run a QA command and persist a versioned evidence manifest."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from transbridge.quality import run_with_evidence  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".qa-evidence"), help="QA run directory root")
    parser.add_argument("--input", action="append", default=[], help="Additional required repository input")
    parser.add_argument("--artifact", action="append", type=Path, default=[], help="Artifact to snapshot after the run")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after --")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        _parser().error("a command is required after --")
    inputs = ["pyproject.toml", "uv.lock", *args.input]
    outcome = run_with_evidence(
        command,
        repository_root=REPOSITORY_ROOT,
        output_root=REPOSITORY_ROOT / args.output,
        input_paths=inputs,
        artifact_paths=[REPOSITORY_ROOT / path for path in args.artifact],
    )
    print(outcome.manifest_path)
    return outcome.return_code


if __name__ == "__main__":
    raise SystemExit(main())
