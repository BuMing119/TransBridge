from dataclasses import dataclass

import pytest

from transbridge.application.terminology.errors import DigestCollisionError
from transbridge.application.terminology.identity import (
    DigestCollisionGuard,
    build_key,
    candidate_id,
    canonical_bytes,
    conflict_group_id,
    evidence_id,
    normalize_original,
    normalize_translation,
    term_id,
)
from transbridge.application.terminology.models import ExtractionMethod, TermScope


def test_normalization_is_nfkc_whitespace_and_original_casefold_only() -> None:
    assert normalize_original("  Ｄragon\tSWORD  ") == "dragon sword"
    assert normalize_translation("  龍\n  劍！ ") == "龍 劍!"
    assert normalize_translation("ABC") != normalize_translation("abc")
    assert normalize_original("word!") != normalize_original("word")


def test_canonical_serialization_ignores_mapping_order() -> None:
    assert canonical_bytes({"b": 2, "a": 1}) == canonical_bytes({"a": 1, "b": 2})


def test_stable_ids_ignore_scan_order_but_preserve_business_line() -> None:
    evidence_a = evidence_id(
        project_id="project-1",
        variant_id="variant-1",
        source_chain=("source-b", "source-a"),
        entry_key="entry-1",
        original=" Dragon ",
        translation="龙",
    )
    evidence_b = evidence_id(
        project_id="project-1",
        variant_id="variant-1",
        source_chain=("source-a", "source-b"),
        entry_key="entry-1",
        original="dragon",
        translation="龙",
    )
    assert evidence_a == evidence_b

    candidate_a = candidate_id(
        evidence_ids=("e2", "e1"),
        original="Dragon",
        translation="龙",
        scope=TermScope.project(),
        extraction_method=ExtractionMethod.DETERMINISTIC_NAME,
        algorithm_version="v1",
    )
    candidate_b = candidate_id(
        evidence_ids=("e1", "e2"),
        original="dragon",
        translation="龙",
        scope=TermScope.project(),
        extraction_method=ExtractionMethod.DETERMINISTIC_NAME,
        algorithm_version="v1",
    )
    assert candidate_a == candidate_b

    assert conflict_group_id(project_id="project-1", variant_id="variant-1", original="Dragon") != (
        conflict_group_id(project_id="project-1", variant_id="variant-2", original="Dragon")
    )


def test_term_identity_ignores_translation_but_preserves_scope_and_original() -> None:
    base = term_id(project_id="project-1", variant_id="variant-1", scope=TermScope.project(), original="Dragon")
    same_after_translation_edit = term_id(
        project_id="project-1", variant_id="variant-1", scope=TermScope.project(), original="dragon"
    )
    replacement = term_id(project_id="project-1", variant_id="variant-1", scope=TermScope.project(), original="Wyrm")
    plugin_exception = term_id(
        project_id="project-1", variant_id="variant-1", scope=TermScope.plugin("A.esm"), original="Dragon"
    )

    assert base == same_after_translation_edit
    assert base != replacement
    assert base != plugin_exception


@dataclass(frozen=True)
class _BuildSnapshot:
    semantic: str
    run_id: str
    captured_at: str
    ui_order: tuple[str, ...]

    def canonical_payload(self) -> dict[str, object]:
        return {"semantic": self.semantic, "sources": self.ui_order}


def test_build_key_consumes_story_01_canonical_payload_and_excludes_observation_identity() -> None:
    first = _BuildSnapshot("same", "run-1", "now", ("b", "a"))
    second = _BuildSnapshot("same", "run-2", "later", ("a", "b"))

    assert build_key(first) == build_key(second)
    assert build_key(first) != build_key(_BuildSnapshot("changed", "run-1", "now", ("a", "b")))


def test_injected_hash_provider_exposes_collision_by_comparing_canonical_payload() -> None:
    guard = DigestCollisionGuard()
    constant_hash = lambda payload: "constant"  # noqa: E731

    evidence_id(
        project_id="project-1",
        variant_id="variant-1",
        source_chain=("source-1",),
        entry_key="entry-1",
        original="Dragon",
        translation="龙",
        hash_provider=constant_hash,
        collision_guard=guard,
    )
    with pytest.raises(DigestCollisionError):
        evidence_id(
            project_id="project-1",
            variant_id="variant-1",
            source_chain=("source-1",),
            entry_key="entry-2",
            original="Sword",
            translation="剑",
            hash_provider=constant_hash,
            collision_guard=guard,
        )
