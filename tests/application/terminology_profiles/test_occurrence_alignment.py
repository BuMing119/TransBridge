from dataclasses import replace

import pytest

from transbridge.application.terminology_profiles.models import ProfileTermMapping, TerminologyProfileContent
from transbridge.application.terminology_profiles.occurrence_alignment import (
    AlignmentDecision,
    FrozenAlignmentResolver,
    validate_alignment_decision,
)
from transbridge.application.terminology_profiles.projection import TerminologyProfileProjector


class Resolver:
    def __init__(self, selected=(), certain=True):
        self.requests = []
        self.selected, self.certain = selected, certain

    def resolve(self, request):
        self.requests.append(request)
        return AlignmentDecision(self.selected, self.certain, "根据双语上下文选择")


def project(original, text, *mappings, resolver=None):
    return TerminologyProfileProjector(resolver=resolver).project(
        entry_key="entry",
        original=original,
        common_translation=text,
        content=TerminologyProfileContent(tuple(ProfileTermMapping(*mapping) for mapping in mappings)),
    )


@pytest.mark.parametrize("old,new", [("红卫", "红卫人"), ("诺德", "诺德人"), ("召唤", "召唤系")])
def test_already_target_spelling_is_not_expanded_again(old, new):
    result = project("Term", new, ("Term", new, old))
    assert result.translation == new
    assert result.diagnostics == ()


def test_balanced_repeated_occurrences_take_fast_path_without_model():
    resolver = Resolver()
    result = project("Nord and Nord", "诺德与诺德", ("Nord", "诺德人", "诺德"), resolver=resolver)
    assert result.translation == "诺德人与诺德人"
    assert not resolver.requests


def test_unknown_long_name_is_flagged_by_old_plus_existing_target_count():
    result = project("A true Nord knows Renod Even-Toned", "真正的诺德人认识雷诺德·准音", ("Nord", "诺德人", "诺德"))
    assert result.translation == "真正的诺德人认识雷诺德·准音"
    assert [item.code for item in result.diagnostics] == ["occurrence_alignment_required"]
    assert "原文 1 处，旧译名 1 处，目标译名 1 处" in result.diagnostics[0].message


def test_known_long_pair_excludes_unrelated_substring_without_entity_category():
    resolver = Resolver()
    result = project(
        "A true Nord knows Renod Even-Toned",
        "真正的诺德认识雷诺德·准音",
        ("Nord", "诺德人", "诺德"),
        ("Renod Even-Toned", "雷诺德·准音", "雷诺德·准音"),
        resolver=resolver,
    )
    assert result.translation == "真正的诺德人认识雷诺德·准音"
    assert not resolver.requests


def test_compositional_noop_long_name_does_not_block_short_mapping():
    result = project(
        "Cyrodilic Hood",
        "西罗帝尔兜帽",
        ("Cyrodilic", "赛瑞迪尔", "西罗帝尔"),
        ("Cyrodilic Hood", "西罗帝尔兜帽", "西罗帝尔兜帽"),
    )
    assert result.translation == "赛瑞迪尔兜帽"
    assert result.diagnostics == ()


def test_exact_long_term_owns_both_source_and_translation_and_target_is_stable():
    mappings = (("Conjuration Staff", "召唤法杖", "召唤系法杖"), ("Conjuration", "召唤系", "召唤"))
    first = project("Conjuration Staff", "召唤系法杖", *mappings)
    second = project("Conjuration Staff", first.translation, *mappings)
    assert first.translation == second.translation == "召唤法杖"
    assert not first.diagnostics and not second.diagnostics


def test_completed_long_target_in_sentence_protects_components_but_not_separate_occurrence():
    mappings = (("Conjuration Staff", "召唤法杖", "召唤系法杖"), ("Conjuration", "召唤系", "召唤"))
    result = project("Use Conjuration Staff for Conjuration", "使用召唤法杖进行召唤", *mappings)
    assert result.translation == "使用召唤法杖进行召唤系"
    assert not result.diagnostics


def test_same_span_shared_with_unchanged_term_is_ambiguous_even_if_counts_match():
    result = project("Nord and Renod", "诺德", ("Nord", "诺德人", "诺德"), ("Renod", "诺德", "诺德"))
    assert result.translation == "诺德"
    assert result.diagnostics[0].code == "occurrence_alignment_required"


def test_shorter_target_is_not_treated_as_completed_inside_old_spelling():
    assert project("Rielle", "瑞尔勒", ("Rielle", "瑞尔", "瑞尔勒")).translation == "瑞尔"


def test_model_can_resolve_mismatch_without_imposing_source_count_quota():
    resolver = Resolver((0, 1))
    result = project("A Nord meets one", "诺德遇见另一位诺德", ("Nord", "诺德人", "诺德"), resolver=resolver)
    assert result.translation == "诺德人遇见另一位诺德人"
    assert len(resolver.requests) == 1
    count = resolver.requests[0].counts[0]
    assert (count.source_count, count.old_count, count.target_count) == (1, 2, 0)


def test_model_can_reject_all_candidates_certainly_or_leave_uncertain():
    for certain in (True, False):
        result = project("Nord", "诺德与雷诺德", ("Nord", "诺德人", "诺德"), resolver=Resolver((), certain))
        assert result.translation == "诺德与雷诺德"
        assert bool(result.diagnostics) is not certain


def test_frozen_replay_requires_exact_original_translation_scope_and_mapping():
    resolver = Resolver((0,))
    project("Nord", "诺德与雷诺德", ("Nord", "诺德人", "诺德"), resolver=resolver)
    request = resolver.requests[0]
    decision = AlignmentDecision((0,), True, "第一处对应原文")
    frozen = FrozenAlignmentResolver(((request, decision),))
    assert frozen.resolve(request) == decision
    for changed in (
        replace(request, original="Another Nord"),
        replace(request, translation="诺德与雷诺德！"),
        replace(request, plugin_id="other.esm"),
        replace(request, candidates=(replace(request.candidates[0], translation="北地人"),)),
    ):
        assert frozen.resolve(changed) is None


@pytest.mark.parametrize("ids", [[True], [-1], [50], [0, 0]])
def test_invalid_model_ids_are_rejected(ids):
    resolver = Resolver()
    project("Nord", "诺德与雷诺德", ("Nord", "诺德人", "诺德"), resolver=resolver)
    with pytest.raises(ValueError):
        validate_alignment_decision(
            {"entry_key": "entry", "selected_ids": ids, "certain": True, "rationale": "理由"}, resolver.requests[0]
        )


def test_overlap_is_not_resolved_by_equal_counts_alone():
    result = project(
        "Use Daedra Silk",
        "使用迪德拉之丝",
        ("Daedra", "魔族", "迪德拉"),
        ("Daedra Silk", "魔族丝绸", "迪德拉之丝"),
    )
    assert result.translation == "使用迪德拉之丝"
    assert result.diagnostics[0].code == "occurrence_alignment_required"


def test_equal_counts_do_not_claim_to_solve_unknown_semantic_collisions():
    # Explicitly document the accepted heuristic limitation: no full glossary/alignment proof.
    result = project("A Nord spoke", "雷诺德说话了", ("Nord", "诺德人", "诺德"))
    assert result.translation == "雷诺德人说话了"
    assert not result.diagnostics


def test_explicit_binding_wins_over_inferred_long_candidate():
    from transbridge.application.terminology_profiles.models import ProfileOccurrenceBinding

    short = ProfileTermMapping("Daedra", "魔族", "迪德拉")
    content = TerminologyProfileContent(
        (short, ProfileTermMapping("Daedra Silk", "魔族丝绸", "迪德拉之丝")),
        bindings=(ProfileOccurrenceBinding("entry", short.term_key, 0, 3, "迪德拉"),),
    )
    result = TerminologyProfileProjector().project(
        entry_key="entry", original="Daedra Silk", common_translation="迪德拉之丝", content=content
    )
    assert result.translation == "魔族之丝"
    assert not result.diagnostics


def test_stale_binding_is_checked_even_when_expected_text_equals_target():
    from transbridge.application.terminology_profiles.models import ProfileOccurrenceBinding

    mapping = ProfileTermMapping("Nord", "诺德人", "诺德")
    result = TerminologyProfileProjector().project(
        entry_key="entry",
        original="Nord",
        common_translation="这位诺德人",
        content=TerminologyProfileContent(
            (mapping,), bindings=(ProfileOccurrenceBinding("entry", mapping.term_key, 0, 3, "诺德人"),)
        ),
    )
    assert result.translation == "这位诺德人"
    assert result.diagnostics[0].code == "binding_text_changed"
