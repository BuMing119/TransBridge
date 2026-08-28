from __future__ import annotations

import pytest

from transbridge.application.io import FormatId
from transbridge.application.projects.source_registry import (
    BilingualCapability,
    SourceKind,
    SourceRegistration,
    SourceRegistrySnapshot,
    SourceRelation,
    SourceRelationKind,
    migrate_legacy_source_registry,
    plugin_source_location,
)


def test_legacy_registry_uses_stable_ids_separate_from_fingerprints_and_builds_unique_relation() -> None:
    fingerprint = "f" * 64
    sources = (
        {
            "source_id": fingerprint,
            "format_id": "plugin.sse",
            "location": "C:/mods/base.esp",
            "fingerprint": fingerprint,
            "role": "primary",
        },
        {
            "source_id": "legacy:translation",
            "format_id": "xml.eet",
            "location": "C:/mods/base.xml",
            "fingerprint": "e" * 64,
            "role": "migration",
        },
    )

    first = migrate_legacy_source_registry("project-1", sources)
    reordered = migrate_legacy_source_registry("project-1", reversed(sources))

    assert {item.source_id for item in first.sources} == {item.source_id for item in reordered.sources}
    assert all(item.source_id not in {fingerprint, "legacy:translation"} for item in first.sources)
    assert len(first.relations) == 1
    assert first.relations[0].kind is SourceRelationKind.TRANSLATION_FOR
    assert first.diagnostics == ()


def test_registry_rejects_dangling_self_referential_and_cyclic_relations() -> None:
    def source(source_id: str, location: str) -> SourceRegistration:
        return SourceRegistration(
            source_id,
            True,
            FormatId.XML_EET,
            location,
            SourceKind.BILINGUAL,
            BilingualCapability.SELF_CONTAINED,
        )

    first = source("source-a", "C:/mods/a.xml")
    second = source("source-b", "C:/mods/b.xml")
    with pytest.raises(ValueError, match="dangling"):
        SourceRegistrySnapshot(
            (first,),
            (SourceRelation("rel-a", SourceRelationKind.TRANSLATION_FOR, "source-a", "missing"),),
        )
    with pytest.raises(ValueError, match="self-referential"):
        SourceRelation("rel-self", SourceRelationKind.TRANSLATION_FOR, "source-a", "source-a")
    with pytest.raises(ValueError, match="acyclic"):
        SourceRegistrySnapshot(
            (first, second),
            (
                SourceRelation("rel-a", SourceRelationKind.TRANSLATION_FOR, "source-a", "source-b"),
                SourceRelation("rel-b", SourceRelationKind.TRANSLATION_FOR, "source-b", "source-a"),
            ),
        )


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ({"type": "esp", "path": "D:/mods/legacy.esp"}, "D:/mods/legacy.esp"),
        (
            {"format_id": "plugin.sse", "location": "D:/mods/v2.esm", "role": "primary"},
            "D:/mods/v2.esm",
        ),
        (
            {
                "source_id": "source-v3",
                "enabled": True,
                "format_id": "plugin.sse",
                "location": "D:/mods/v3.esl",
                "kind": "plugin",
                "bilingual_capability": "none",
                "format_options": {},
            },
            "D:/mods/v3.esl",
        ),
        (
            {
                "source_id": "source-disabled",
                "enabled": False,
                "format_id": "plugin.sse",
                "location": "D:/mods/disabled.esp",
                "kind": "plugin",
            },
            None,
        ),
        ({"format_id": "xml.eet", "location": "D:/mods/translation.xml"}, None),
    ),
)
def test_plugin_source_location_supports_legacy_v2_and_current_project_shapes(source, expected) -> None:
    assert plugin_source_location(source) == expected
