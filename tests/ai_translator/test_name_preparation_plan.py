from dataclasses import replace
from types import SimpleNamespace

import pytest

from transbridge.ai_translator.exact_term_lookup import ExactTermLookup
from transbridge.ai_translator.name_preparation_authority import SourceNameAuthority
from transbridge.ai_translator.name_preparation_plan import (
    NameAuthority,
    collect_project_names,
    eligible_names,
    plan_project_names,
    resolve_project_names,
)
from transbridge.ai_translator.term_formats import TermEntry
from transbridge.application.terminology.effective import TerminologyLookupContext
from transbridge.converter.context_categories import ROUND1_CATEGORIES, name_category
from transbridge.converter.translation_entry import TranslationEntry


def _entry(key, original="Dawn", context="WEAP:FULL", *, translation="", stage=0, plugin="A.esp"):
    return TranslationEntry(key, key, original, translation, stage, context, form_id_with_plugin=f"000001|{plugin}")


def _source(key, *entries):
    return SimpleNamespace(key=key, translate_entries=entries)


class _Manager:
    _effective_loader = None

    def __init__(self, terms=()):
        self.terms = terms

    def lookup_context_for_entry(self, entry):
        return TerminologyLookupContext("project", "variant", entry.form_id_with_plugin.split("|")[1])

    def _effective_terms(self, context):
        return list(self.terms)

    def _get_term_matcher_map(self, context):
        return {
            spelling: (term.term, term.translation, term.case_sensitive)
            for term in self.terms
            for spelling in (term.term, *term.variants)
        }


@pytest.mark.parametrize("context", ["WEAP:FULL", "ARMO:FULL", "SPEL:FULL", "PERK:FULL", "QUST:FULL", "SCRL:FULL"])
def test_names_include_items_skills_quests_and_scroll_titles(context):
    assert name_category(context) is not None
    assert name_category(context + "|metadata") == name_category(context)
    assert ROUND1_CATEGORIES["书名"] == {"BOOK:FULL", "SCRL:FULL"}


@pytest.mark.parametrize("context", ["WEAP:DESC", "SPEL:DESC", "BOOK:DESC", "MESG:ITXT", "ACTI:RNAM", "FLOR:RNAM"])
def test_name_barrier_does_not_include_descriptions_or_activation_prompts(context):
    assert name_category(context) is None


def test_duplicate_name_group_requires_identical_spelling_category_and_constraints():
    one = _source("A", _entry("one"), _entry("two", "dawn"), _entry("three", context="LCTN:FULL"))
    two = _source("B", _entry("four"), _entry("five", "Known"))
    three = _source("C", _entry("six"))
    groups = plan_project_names(
        (one, two, three),
        overwrite=False,
        authority_for=lambda task, entry: NameAuthority(exact_translation="特殊译法" if task.key == "C" else None),
    )
    assert len(groups) == 5
    assert [(item.source_key, item.entry.key) for item in groups[0].occurrences] == [("A", "one"), ("B", "four")]
    assert groups[-1].authority.exact_translation == "特殊译法"


def test_name_plan_obeys_overwrite_and_shared_stage_policy():
    entries = tuple(
        _entry(str(stage), stage=stage, translation="已有" if stage else "") for stage in (0, 1, 2, 3, 5, 9, -1)
    )
    assert [entry.stage for entry in eligible_names(_source("A", *entries), overwrite=False)] == [0]
    assert [entry.stage for entry in eligible_names(_source("A", *entries), overwrite=True)] == [0, 1, 2, 3, 5]


def test_exact_lookup_preserves_input_alias_case_policy_and_priority():
    lookup = ExactTermLookup([
        TermEntry("River Wood", "旧", "old"),
        TermEntry("River Wood", "溪木", "new", variants=["Riverwood"]),
        TermEntry("US", "美国", "case", case_sensitive=True),
    ])
    assert lookup.translation_for("  RIVER\tWOOD  ") == "溪木"
    assert lookup.translation_for("Ｒｉｖｅｒｗｏｏｄ") == "溪木"
    assert lookup.translation_for("US") == "美国"
    assert lookup.translation_for("us") is None


def test_existing_name_reuse_is_local_valid_and_does_not_reuse_overwrite_targets():
    known = _entry("known", translation="破晓", stage=1)
    target = _entry("target")
    ignored = [replace(known, id=str(stage), key=str(stage), entry_key=None, stage=stage) for stage in (0, 2, 9, -1)]
    authority = SourceNameAuthority(_Manager(), [known, *ignored], excluded={target.identity})
    assert authority.for_entry(target).existing_translations == ("破晓",)
    assert authority.for_entry(replace(target, form_id_with_plugin="000002|B.esp")).existing_translations == ()
    assert (
        SourceNameAuthority(_Manager(), [known], excluded={known.identity}).for_entry(target).existing_translations
        == ()
    )
    assert SourceNameAuthority(_Manager(), ignored, excluded=set()).for_entry(target).existing_translations == ()


def test_effective_term_authority_wins_over_existing_local_name_and_tracks_required_subterms():
    terms = [TermEntry("Dawn", "黎明", "published"), TermEntry("Iron", "铁", "published")]
    known = _entry("known", translation="破晓", stage=3)
    authority = SourceNameAuthority(_Manager(terms), [known], excluded=set())
    direct = authority.for_entry(_entry("target"))
    assert direct.exact_translation == "黎明"
    assert direct.existing_translations == ()
    assert authority.for_entry(_entry("item", "Iron Sword")).required_terms == (("Iron", "铁"),)
    assert authority.for_entry(_entry("fragment", "Ironbound Sword")).required_terms == ()


def test_existing_conflicting_names_remain_explicit_and_invalid_syntax_is_not_reused():
    known = _entry("known", translation="破晓", stage=1)
    other = _entry("other", translation="黎明", stage=5)
    invalid = _entry("invalid", "Dawn {0}", translation="破晓", stage=1)
    authority = SourceNameAuthority(_Manager(), [known, other, invalid], excluded=set())
    assert set(authority.for_entry(_entry("target")).existing_translations) == {"破晓", "黎明"}
    assert authority.for_entry(_entry("syntax", "Dawn {0}")).existing_translations == ()


def test_local_conflicting_translations_keep_each_selected_name_independent():
    authority = NameAuthority(existing_translations=("破晓", "黎明"))
    groups = plan_project_names(
        [_source("A", _entry("first"), _entry("second"))],
        overwrite=False,
        authority_for=lambda *_: authority,
    )
    assert len(groups) == 2
    assert all(len(group.occurrences) == 1 for group in groups)


def test_planner_checks_cancellation_while_scanning_one_large_source():
    class Cancelled(Exception):
        pass

    checkpoints = []

    def checkpoint():
        checkpoints.append(None)
        if len(checkpoints) == 3:
            raise Cancelled()

    with pytest.raises(Cancelled):
        plan_project_names(
            [_source("A", *(_entry(str(index)) for index in range(100)))],
            overwrite=False,
            authority_for=lambda *_: NameAuthority(),
            checkpoint=checkpoint,
        )
    assert len(checkpoints) == 3


def test_literal_collection_finishes_before_authority_lookup_and_retains_source_order():
    scanned = []

    class Entries:
        def __iter__(self):
            for index in range(600):
                scanned.append(index)
                yield _entry(str(index), "Dawn" if index % 2 == 0 else "Iron Sword")

    first = _source("A")
    first.translate_entries = Entries()
    second = _source("B", _entry("second", plugin="B.esp"), _entry("unique", "Night", plugin="B.esp"))
    progress = []
    candidates = collect_project_names(
        (first, second),
        overwrite=False,
        progress=lambda task, current, total: progress.append((task.key, current, total)),
    )
    assert len(candidates) == 3
    assert len(candidates[0].occurrences) == 301
    assert len(candidates[1].occurrences) == 300
    assert progress[0] == ("A", 0, 0)
    assert ("A", 256, 0) in progress and ("A", 600, 600) in progress
    assert progress[-1] == ("B", 2, 2)
    checks = []

    def authority_for(occurrence, repeated):
        assert len(scanned) == 600, "term lookup may only start after collection completes"
        checks.append((occurrence.entry.original, repeated))
        return NameAuthority(exact_translation="插件黎明" if occurrence.source_key == "B" else None)

    groups = resolve_project_names(candidates, authority_for=authority_for)
    assert [group.representative.entry.original for group in groups] == ["Dawn", "Iron Sword", "Dawn", "Night"]
    assert checks[-1] == ("Night", False)
    assert all(repeated for original, repeated in checks if original != "Night")
