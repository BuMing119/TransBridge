"""Canonical filesystem path authorization."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import os
from pathlib import Path

from .hitl import AuthorizationDecision


@dataclass(frozen=True, slots=True)
class PathGrant:
    root: Path
    allow_create: bool = False


def _is_within(target: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(target), str(root))) == str(root)
    except ValueError:
        return False


class PathAuthorizationPolicy:
    """Authorizes resolved paths against explicit canonical root grants."""

    def __init__(self, grants: Iterable[PathGrant]) -> None:
        self._grants = tuple(
            PathGrant(Path(grant.root).resolve(strict=True), grant.allow_create)
            for grant in grants
        )

    def authorize(
        self,
        value: str | os.PathLike[str],
        *,
        working_directory: Path | None = None,
        for_creation: bool = False,
    ) -> AuthorizationDecision:
        raw = Path(value)
        if not raw.is_absolute():
            if working_directory is None:
                return AuthorizationDecision(False, "PATH_BASE_REQUIRED", "相对路径缺少工作目录")
            raw = Path(working_directory) / raw
        try:
            if raw.exists():
                target = raw.resolve(strict=True)
            elif for_creation:
                target = raw.parent.resolve(strict=True) / raw.name
            else:
                return AuthorizationDecision(False, "PATH_NOT_FOUND", "目标路径不存在")
        except (OSError, RuntimeError):
            return AuthorizationDecision(False, "PATH_RESOLUTION_FAILED", "目标路径无法安全解析")

        for grant in self._grants:
            if for_creation and not grant.allow_create:
                continue
            if _is_within(target, grant.root):
                return AuthorizationDecision(True, "PATH_ALLOWED", "路径已授权")
        return AuthorizationDecision(False, "PATH_OUTSIDE_GRANT", "目标路径超出授权范围")
