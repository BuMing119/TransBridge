"""Deep local paths for real filesystem regressions without user data."""

from pathlib import Path


def deep_directory(tmp_path: Path, minimum_length: int) -> Path:
    path = tmp_path / "路径 100% #&"
    while len(str(path)) < minimum_length:
        path /= "x" * min(70, minimum_length - len(str(path)))
    return path
