from __future__ import annotations

import pytest

from transbridge.application.projects import (
    ProjectCatalogEntry,
    ProjectCatalogQuery,
    ProjectCatalogSnapshot,
)


class _Catalog:
    def list_projects(self) -> ProjectCatalogSnapshot:
        return ProjectCatalogSnapshot()


def test_project_catalog_contract_is_narrow_and_runtime_checkable() -> None:
    catalog = _Catalog()

    assert isinstance(catalog, ProjectCatalogQuery)
    assert catalog.list_projects() == ProjectCatalogSnapshot()


def test_project_catalog_entry_requires_safe_availability_reason_pairing() -> None:
    with pytest.raises(ValueError, match="must not carry"):
        ProjectCatalogEntry("project-1", "工程", "/safe/project.json", True, True, "错误")
    with pytest.raises(ValueError, match="require"):
        ProjectCatalogEntry("project-1", "工程", "/safe/project.json", False, False)


def test_project_catalog_snapshot_serializes_display_fields_and_diagnostics() -> None:
    entry = ProjectCatalogEntry(
        "project-1",
        "工程",
        "/safe/project.json",
        active=True,
        available=False,
        reason="工程记录不存在。",
    )

    assert ProjectCatalogSnapshot((entry,)).to_dict() == {
        "projects": [
            {
                "project_id": "project-1",
                "name": "工程",
                "path": "/safe/project.json",
                "active": True,
                "available": False,
                "reason": "工程记录不存在。",
            }
        ],
        "diagnostics": [],
    }
