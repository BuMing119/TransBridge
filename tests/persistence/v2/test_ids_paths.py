"""Opaque identity and path-confinement contracts."""

from __future__ import annotations

import os

import pytest

from transbridge.persistence.v2 import (
    PathBoundaryError,
    ProjectId,
    ProjectRef,
    RepositoryPaths,
    VariantId,
    VariantRef,
)

from .fakes import MemoryFilesystem

ROOT = os.path.abspath(os.path.join(os.sep, "transbridge-v2-tests"))


@pytest.mark.parametrize(
    "value",
    ("", ".", "..", "../escape", "a/b", "a\\b", "/absolute", "C:\\absolute", "id:", "trailing."),
)
def test_opaque_id_rejects_path_and_noncanonical_values(value: str) -> None:
    with pytest.raises(ValueError):
        ProjectId(value)


def test_encoded_identity_is_used_for_root_confined_filename() -> None:
    filesystem = MemoryFilesystem()
    ref = ProjectRef(ProjectId("project.alpha-1"))
    path = RepositoryPaths(ROOT, filesystem).record(ref)

    assert ref.identity.value not in os.path.basename(path)
    assert os.path.basename(path).startswith("id-")
    assert os.path.commonpath((ROOT, path)) == ROOT


def test_canonical_symlink_or_junction_escape_is_rejected() -> None:
    filesystem = MemoryFilesystem()
    projects_directory = os.path.join(ROOT, "projects")
    filesystem.canonical_aliases[projects_directory] = os.path.abspath(os.path.join(os.sep, "outside-vault"))
    paths = RepositoryPaths(ROOT, filesystem)

    with pytest.raises(PathBoundaryError):
        paths.record(ProjectRef(ProjectId("safe-id")))


def test_persistence_root_must_be_absolute() -> None:
    with pytest.raises(PathBoundaryError):
        RepositoryPaths("relative-root", MemoryFilesystem())


def test_same_variant_id_is_namespaced_by_encoded_project_id() -> None:
    filesystem = MemoryFilesystem()
    paths = RepositoryPaths(ROOT, filesystem)
    first = paths.record(VariantRef(VariantId("main"), ProjectId("project-a")))
    second = paths.record(VariantRef(VariantId("main"), ProjectId("project-b")))

    assert first != second
    assert ProjectId("project-a").encoded in first
    assert ProjectId("project-b").encoded in second
