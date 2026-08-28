"""Staged, collision-aware publication of derived artifacts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import os
from pathlib import Path
import tempfile


class ArtifactPublishPolicy(StrEnum):
    FAIL_IF_EXISTS = "fail-if-exists"
    RENAME = "rename"
    OVERWRITE = "overwrite"


class ArtifactTargetExistsError(FileExistsError):
    pass


@dataclass(frozen=True, slots=True)
class PublishedFile:
    path: Path
    size: int
    sha256: str


def publish_bytes(
    content: bytes,
    target: str | Path,
    *,
    policy: ArtifactPublishPolicy = ArtifactPublishPolicy.FAIL_IF_EXISTS,
) -> PublishedFile:
    return publish_staged(target, lambda path: path.write_bytes(content), policy=policy)


def publish_staged(
    target: str | Path,
    writer: Callable[[Path], object],
    *,
    policy: ArtifactPublishPolicy = ArtifactPublishPolicy.FAIL_IF_EXISTS,
) -> PublishedFile:
    target_path = Path(target)
    if not target_path.name:
        raise ValueError("artifact target must name a file")
    policy = ArtifactPublishPolicy(policy)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".staging",
        dir=target_path.parent,
    )
    os.close(descriptor)
    staging = Path(staging_name)
    published: Path | None = None
    try:
        writer(staging)
        if not staging.is_file():
            raise OSError("artifact writer did not produce a staging file")
        with staging.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        size = staging.stat().st_size
        if policy is ArtifactPublishPolicy.OVERWRITE:
            os.replace(staging, target_path)
            published = target_path
        elif policy is ArtifactPublishPolicy.FAIL_IF_EXISTS:
            published = _link_exclusive(staging, target_path)
        else:
            published = _publish_renamed(staging, target_path)
        return PublishedFile(published, size, digest)
    finally:
        if staging.exists():
            staging.unlink()


def _link_exclusive(staging: Path, target: Path) -> Path:
    try:
        os.link(staging, target)
    except FileExistsError as exc:
        raise ArtifactTargetExistsError(f"artifact target already exists: {target}") from exc
    return target


def _publish_renamed(staging: Path, target: Path) -> Path:
    for index in range(0, 10_000):
        candidate = target if index == 0 else target.with_name(f"{target.stem}-{index:03d}{target.suffix}")
        try:
            os.link(staging, candidate)
        except FileExistsError:
            continue
        return candidate
    raise ArtifactTargetExistsError(f"no available deterministic artifact name near: {target}")


__all__ = [
    "ArtifactPublishPolicy",
    "ArtifactTargetExistsError",
    "PublishedFile",
    "publish_bytes",
    "publish_staged",
]
