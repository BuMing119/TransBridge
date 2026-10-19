"""Label projections copy only label metadata and retain nested JSON shapes."""

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projections import project_projection
from transbridge.application.projects.models import ActiveProject
from transbridge.persistence.v2 import (
    SCHEMA_VERSION,
    EntityKind,
    ProjectDto,
    ProjectId,
    SchemaEnvelope,
    SourceFingerprint,
    VariantAggregate,
    VariantEntryState,
    VariantId,
    VariantRef,
    VariantSnapshot,
)


def test_label_data_and_project_projection_are_defensive_nested_copies():
    ref = VariantRef(VariantId("variant"), ProjectId("project"))
    namespace = SourceNamespace.from_fingerprint("json", "a" * 64)
    library = {"review": {"rules": [["pair", 1]], "color": "blue", "enabled": True}}
    snapshot = VariantSnapshot(
        ref,
        (SourceFingerprint(namespace, "a" * 64),),
        (VariantEntryState(EntryKey(namespace, "key"), "translated", labels=("review",)),),
        label_library=tuple(library.items()),
    )
    project = ProjectDto(
        SchemaEnvelope(
            SCHEMA_VERSION,
            EntityKind.PROJECT,
            ref.project_id.value,
            0,
            {
                "name": "Project",
                "sources": [],
                "variant_ids": [ref.identity.value],
                "active_variant_id": ref.identity.value,
            },
        )
    )
    active = ActiveProject(project, VariantAggregate(snapshot), ref, 0, 0)

    copied = snapshot.label_library_data()
    copied["review"]["rules"][0].append("changed")
    projection = project_projection(active)
    values = projection.to_dict()["values"]

    assert snapshot.label_library_data() == library
    assert snapshot.to_dto().envelope.data["label_library"] == library
    assert values["label_library"] == library
    assert values["entries"][0]["translation"] == "translated"
    assert values["entries"][0]["labels"] == ["review"]
    values["label_library"]["review"]["rules"][0].append("mutated projection")
    assert projection.to_dict()["values"]["label_library"] == library
    assert active.variant.snapshot().label_library_data() == library
