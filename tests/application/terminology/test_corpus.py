from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.io.stage_policy import Stage
from transbridge.application.projects.source_registry import SourceRelation, SourceRelationKind
from transbridge.application.terminology.corpus import (
    CorpusEntry,
    EvidenceAssembler,
    SourceCorpusFragment,
)
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.persistence.v2.variant import SourceFingerprint, VariantEntryState, VariantSnapshot

_SHA = "a" * 64


def _entry(
    namespace: str,
    local_key: str,
    original: str,
    translation: str,
    stage: Stage = Stage.TRANSLATED,
    context: str = "NPC_:FULL",
) -> CorpusEntry:
    return CorpusEntry(EntryKey(SourceNamespace(namespace), local_key), original, translation, stage, context)


def _fragment(source_id: str, entries: tuple[CorpusEntry, ...], plugin_scope: str | None = None):
    return SourceCorpusFragment(source_id, "test", _SHA, entries, plugin_scope)


def _variant(*states: VariantEntryState) -> VariantSnapshot:
    namespaces = tuple(sorted({state.entry_key.namespace for state in states}))
    return VariantSnapshot(
        VariantRef(VariantId("main"), ProjectId("project-1")),
        tuple(SourceFingerprint(namespace, _SHA) for namespace in namespaces),
        tuple(states),
        revision=1,
    )


def test_eligibility_requires_bilingual_visible_non_questionable_entries() -> None:
    fragment = _fragment(
        "source-a",
        (
            _entry("source:a", "ok", "Dragon", "龙"),
            _entry("source:a", "original-empty", " ", "龙"),
            _entry("source:a", "translation-empty", "Dragon", ""),
            _entry("source:a", "hidden", "Dragon", "龙", Stage.HIDDEN),
            _entry("source:a", "questionable", "Dragon", "龙", Stage.QUESTIONABLE),
        ),
    )

    result = EvidenceAssembler().assemble(
        project_id="project-1",
        variant_id="main",
        fragments=(fragment,),
        relations=(),
        variant_snapshot=_variant(),
    )

    expected = next(item.entry_key.serialize() for item in fragment.entries if item.entry_key.local_key == "ok")
    assert [item.entry_key for item in result.evidence] == [expected]
    assert dict(result.excluded_reasons) == {
        "hidden": 1,
        "original_empty": 1,
        "questionable": 1,
        "translation_empty": 1,
    }


def test_explicit_relation_aligns_within_source_pair_not_global_local_key() -> None:
    target = _fragment("plugin-a", (_entry("plugin:a", "shared", "Dragon", ""),), "A.esm")
    translated = _fragment("xml-a", (_entry("xml:a", "shared", "Dragon", "龙"),))
    unrelated = _fragment("other", (_entry("other", "shared", "Sword", "剑"),), "B.esm")
    relation = SourceRelation(
        "relation-a",
        SourceRelationKind.TRANSLATION_FOR,
        "xml-a",
        "plugin-a",
    )

    result = EvidenceAssembler().assemble(
        project_id="project-1",
        variant_id="main",
        fragments=(unrelated, translated, target),
        relations=(relation,),
        variant_snapshot=_variant(),
    )

    pairs = {(item.original, item.translation, item.plugin_scope) for item in result.evidence}
    assert pairs == {("Dragon", "龙", "A.esm"), ("Sword", "剑", "B.esm")}
    dragon = next(item for item in result.evidence if item.original == "Dragon")
    assert dragon.source_chain == ("plugin-a", "xml-a")


def test_relation_prefers_complete_entry_key_when_target_has_duplicate_local_keys() -> None:
    translated = _fragment("translation", (_entry("plugin:a", "shared", "Dragon", "龙"),))
    target = _fragment(
        "plugins",
        (
            _entry("plugin:a", "shared", "Dragon", ""),
            _entry("plugin:b", "shared", "Sword", ""),
        ),
    )
    relation = SourceRelation(
        "relation-full-key",
        SourceRelationKind.TRANSLATION_FOR,
        "translation",
        "plugins",
    )

    result = EvidenceAssembler().assemble(
        project_id="project-1",
        variant_id="main",
        fragments=(translated, target),
        relations=(relation,),
        variant_snapshot=_variant(),
    )

    assert [(item.original, item.translation, item.entry_key) for item in result.evidence] == [
        ("Dragon", "龙", EntryKey(SourceNamespace("plugin:a"), "shared").serialize())
    ]
    assert not result.diagnostics


def test_relation_local_key_fallback_refuses_multi_namespace_target() -> None:
    translated = _fragment("translation", (_entry("translation", "shared", "Translated", "译文"),))
    target = _fragment(
        "plugins",
        (
            _entry("plugin:a", "shared", "Dragon", ""),
            _entry("plugin:b", "shared", "Sword", ""),
        ),
    )
    relation = SourceRelation(
        "relation-no-cross-namespace",
        SourceRelationKind.TRANSLATION_FOR,
        "translation",
        "plugins",
    )

    result = EvidenceAssembler().assemble(
        project_id="project-1",
        variant_id="main",
        fragments=(translated, target),
        relations=(relation,),
        variant_snapshot=_variant(),
    )

    assert not result.evidence
    assert dict(result.excluded_reasons) == {"relation_entry_missing": 1}


def test_variant_overlay_uses_complete_entry_key_and_does_not_cross_namespaces() -> None:
    first = _entry("plugin:a", "same", "Dragon", "old")
    second = _entry("plugin:b", "same", "Sword", "other")
    variant = _variant(VariantEntryState(first.entry_key, "current", Stage.REVIEWED))

    result = EvidenceAssembler().assemble(
        project_id="project-1",
        variant_id="main",
        fragments=(
            _fragment("plugin-a", (first,)),
            _fragment("plugin-b", (second,)),
        ),
        relations=(),
        variant_snapshot=variant,
    )

    translations = {item.original: item.translation for item in result.evidence}
    assert translations == {"Dragon": "current", "Sword": "other"}
    assert next(item for item in result.evidence if item.original == "Dragon").from_current_variant
    assert not next(item for item in result.evidence if item.original == "Sword").from_current_variant


def test_n_to_m_relations_preserve_each_target_source_identity() -> None:
    translation = _fragment("shared-xml", (_entry("xml", "one", "Dragon", "龙"),))
    target_a = _fragment("plugin-a", (_entry("a", "one", "Dragon", ""),), "A.esm")
    target_b = _fragment("plugin-b", (_entry("b", "one", "Dragon", ""),), "B.esm")
    relations = (
        SourceRelation("rel-a", SourceRelationKind.TRANSLATION_FOR, "shared-xml", "plugin-a"),
        SourceRelation("rel-b", SourceRelationKind.TRANSLATION_FOR, "shared-xml", "plugin-b"),
    )

    result = EvidenceAssembler().assemble(
        project_id="project-1",
        variant_id="main",
        fragments=(target_b, translation, target_a),
        relations=relations,
        variant_snapshot=_variant(),
    )

    assert len(result.evidence) == 2
    assert {item.plugin_scope for item in result.evidence} == {"A.esm", "B.esm"}
    assert len({item.evidence_id for item in result.evidence}) == 2


def test_variant_overlay_refuses_same_entry_key_when_source_fingerprint_changed() -> None:
    entry = _entry("plugin:a", "same", "Dragon", "parsed")
    stale_variant = VariantSnapshot(
        VariantRef(VariantId("main"), ProjectId("project-1")),
        (SourceFingerprint(entry.entry_key.namespace, "b" * 64),),
        (VariantEntryState(entry.entry_key, "stale", Stage.REVIEWED),),
        revision=1,
    )

    result = EvidenceAssembler().assemble(
        project_id="project-1",
        variant_id="main",
        fragments=(_fragment("plugin-a", (entry,)),),
        relations=(),
        variant_snapshot=stale_variant,
    )

    assert result.evidence[0].translation == "parsed"
    assert result.diagnostics == ("VARIANT_FINGERPRINT_MISMATCH:plugin-a:plugin:a",)
