from __future__ import annotations

from transbridge.application.projects import (
    project_with_active_variant,
    project_with_added_variant,
    project_without_variant,
    variant_catalog,
)
from transbridge.persistence.v2 import ProjectDto, ProjectId, ProjectRef, SchemaEnvelope, VariantId, VariantRef


def _project(*, active: str = "variant-a", revision: int = 4) -> tuple[ProjectDto, ProjectRef]:
    project_ref = ProjectRef(ProjectId("project-a"))
    return (
        ProjectDto(
            SchemaEnvelope(
                2,
                project_ref.kind,
                project_ref.identity.value,
                revision,
                {
                    "name": "Project A",
                    "sources": [],
                    "variant_ids": ["variant-a"],
                    "active_variant_id": active,
                },
            )
        ),
        project_ref,
    )


def test_existing_single_variant_gets_safe_default_display_name() -> None:
    project, _ = _project()

    assert [item.to_dict() for item in variant_catalog(project)] == [
        {"id": "variant-a", "name": "默认", "active": True}
    ]


def test_catalog_names_survive_add_and_delete_without_using_ids_as_labels() -> None:
    project, project_ref = _project()
    review_ref = VariantRef(VariantId("variant-review"), project_ref.identity)

    added = project_with_added_variant(project, review_ref, "审校版")
    switched = project_with_active_variant(added, review_ref)
    removed = project_without_variant(switched, "variant-a")

    assert [(item.variant_id, item.name) for item in variant_catalog(added)] == [
        ("variant-a", "默认"),
        ("variant-review", "审校版"),
    ]
    assert removed.envelope.data["variant_names"] == {"variant-review": "审校版"}


def test_reactivating_same_variant_does_not_inflate_project_revision() -> None:
    project, project_ref = _project(revision=7)
    active_ref = VariantRef(VariantId("variant-a"), project_ref.identity)

    unchanged = project_with_active_variant(project, active_ref)

    assert unchanged.envelope.revision == 7
    assert unchanged.envelope.data == project.envelope.data
