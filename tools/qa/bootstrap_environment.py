"""Rebuild and verify the pinned Python 3.12 project environment with uv."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    lock = REPOSITORY_ROOT / "uv.lock"
    if not lock.is_file():
        print("BLOCKED: uv.lock is missing", file=sys.stderr)
        return 2
    uv = shutil.which("uv")
    if uv is None:
        print("BLOCKED: uv is unavailable", file=sys.stderr)
        return 2

    environment = os.environ.copy()
    environment["UV_PYTHON"] = "3.12.12"
    completed = subprocess.run([uv, "sync", "--frozen"], cwd=REPOSITORY_ROOT, env=environment, check=False)
    if completed.returncode != 0:
        return completed.returncode

    project_python = REPOSITORY_ROOT / ".venv" / "Scripts" / "python.exe"
    if not project_python.is_file():
        print("BLOCKED: .venv/Scripts/python.exe was not created", file=sys.stderr)
        return 2
    probe = subprocess.run(
        [
            str(project_python),
            "-c",
            (
                "import sys; raise SystemExit(0 if sys.version_info[:3] >= (3, 12, 12) "
                "and sys.version_info[:2] == (3, 12) else 2)"
            ),
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    if probe.returncode != 0:
        print("BLOCKED: project environment is not Python 3.12.12+", file=sys.stderr)
    return probe.returncode


if __name__ == "__main__":
    raise SystemExit(main())
