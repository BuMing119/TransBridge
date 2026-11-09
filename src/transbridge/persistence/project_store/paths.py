# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Paths owned by current projects."""

from pathlib import Path

from transbridge.persistence.v2.ids import ProjectId

from .format import PROJECT_FILENAME, PROJECT_ROOT


def project_path(root: str | Path, project: ProjectId) -> Path:
    return Path(root) / PROJECT_ROOT / project.encoded / PROJECT_FILENAME
