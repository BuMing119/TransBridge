"""Read-only directory feasibility and execution-time output preparation."""

from __future__ import annotations

import errno
import os
from pathlib import Path

from transbridge.application.security.paths import PathAuthorizationPolicy

from .filesystem import PublishFilesystemPort


def output_parent_available(target: str) -> bool:
    """A missing directory is usable when its closest existing parent is writable."""
    parent = Path(target).parent
    try:
        while not parent.exists():
            if parent.is_symlink() or parent == parent.parent:
                return False
            parent = parent.parent
        return parent.is_dir() and os.access(parent, os.W_OK)
    except OSError:
        return False


def prepare_output_directory(
    filesystem: PublishFilesystemPort,
    target: str,
    *,
    path_policy: PathAuthorizationPolicy | None = None,
) -> None:
    """Create only the requested output ancestors, rechecking grants and aliases."""
    canonical = filesystem.canonicalize(target)
    if not filesystem.atomic_replace_supported(canonical):
        raise OSError(errno.ENOTSUP, "atomic replace is unavailable for this filesystem")
    missing = []
    parent = Path(canonical).parent
    while not filesystem.exists(str(parent)):
        if parent == parent.parent:
            raise FileNotFoundError("publish target has no existing parent")
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        _authorize(path_policy, str(directory), for_creation=True)
        filesystem.create_directory(str(directory))
        if filesystem.canonicalize(target) != canonical:
            raise FileExistsError("publish output directory changed during preparation")
    _authorize(path_policy, canonical, for_creation=not filesystem.exists(canonical))
    if filesystem.canonicalize(target) != canonical:
        raise FileExistsError("publish output directory changed during preparation")


def _authorize(policy: PathAuthorizationPolicy | None, path: str, *, for_creation: bool) -> None:
    if policy is not None:
        decision = policy.authorize(path, for_creation=for_creation)
        if not decision.allowed:
            raise PermissionError(decision.code)
