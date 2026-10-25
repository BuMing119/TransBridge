from types import SimpleNamespace

import pytest

from transbridge.ai_translator.required_term_matching import MandatoryTermMatcher, match_required_terms
from transbridge.ai_translator.term_database import TermDatabaseManager, TermEntry


def test_real_log_word_fragments_are_not_mandatory_terms():
    terms = {
        "Ria": ("Ria", "利亚", False),
        "Mor": ("Mor", "莫尔", False),
        "Ed": ("Ed", "艾德", False),
        "Aetherial Lantern": ("Aetherial Lantern", "神光水晶灯", False),
        "Morvic": ("Morvic", "莫维克", False),
    }
    assert match_required_terms("Remi's Aetherial Lantern", terms) == {"Aetherial Lantern": "神光水晶灯"}
    assert match_required_terms("Morvic's Boots", terms) == {"Morvic": "莫维克"}
    assert match_required_terms("Translated Book", terms) == {}
    assert match_required_terms("Ria, Mor and Ed.", terms) == {"Ria": "利亚", "Mor": "莫尔", "Ed": "艾德"}


def test_longest_phrase_wins_but_independent_short_term_still_applies():
    terms = {
        "gauntlets": ("gauntlets", "护腕", False),
        "Remi": ("Remi", "蕾米", False),
        "Remi's Gauntlets": ("Remi's Gauntlets", "蕾米的护手", False),
    }
    assert match_required_terms("Remi's Gauntlets", terms) == {"Remi's Gauntlets": "蕾米的护手"}
    assert match_required_terms("Remi's Gauntlets and other gauntlets", terms) == {
        "Remi's Gauntlets": "蕾米的护手",
        "gauntlets": "护腕",
    }


def test_explicit_alias_case_sensitivity_and_non_latin_forms():
    terms = {
        "White Run": ("Whiterun", "白漫", False),
        "US": ("US", "美国", True),
        "巨龙": ("巨龙", "Dragon", False),
        "Ignored": ("Ignored", " ", False),
    }
    assert match_required_terms("WHITE RUN helps us; US 巨龙之剑 Ignored", terms) == {
        "Whiterun": "白漫",
        "US": "美国",
        "巨龙": "Dragon",
    }


def test_reverse_recall_is_preserved_for_translation_but_not_required_validation():
    manager = object.__new__(TermDatabaseManager)
    manager._effective_terms = lambda: [TermEntry(term="Black Briar Meadery", translation="黑棘酿酒坊", source="test")]
    assert manager.match_terms(["Meadery"]) == {"Black Briar Meadery": "黑棘酿酒坊"}
    assert manager.match_terms_for_entry(SimpleNamespace(original="Meadery")) == {}
    assert manager.match_terms_for_entry(SimpleNamespace(original="Black Briar Meadery")) == {
        "Black Briar Meadery": "黑棘酿酒坊",
    }


def test_reused_large_matcher_does_not_revalidate_or_recompile_per_name(monkeypatch):
    from transbridge.ai_translator import required_term_matching

    compiled = []
    compile_pattern = required_term_matching.re.compile

    def record_compile(*args, **kwargs):
        compiled.append(args[0])
        return compile_pattern(*args, **kwargs)

    monkeypatch.setattr(required_term_matching.re, "compile", record_compile)
    terms = {f"Term{i:04d}": (f"Term{i:04d}", f"译法{i}", False) for i in range(1024)}
    matcher = MandatoryTermMatcher(terms)
    assert len(compiled) == 1024

    def unexpected(*_):
        raise AssertionError("a compiled terminology view must not revalidate unchanged terms")

    monkeypatch.setattr(required_term_matching, "valid_term_text", unexpected)
    monkeypatch.setattr(required_term_matching, "valid_term_pair", unexpected)
    for index in range(20):
        assert matcher.match(f"Use Term{index:04d} alongside Term1023") == {
            f"Term{index:04d}": f"译法{index}",
            "Term1023": "译法1023",
        }
    assert len(compiled) == 1024


@pytest.mark.parametrize("text", ["Iron Sword and sword", "IRON SWORD", "US helps us", "İ ı ſ K", "巨龙之剑"])
def test_reusable_matcher_preserves_regex_case_alias_and_overlap_semantics(text):
    terms = {
        "Sword": ("Sword", "剑", False),
        "Iron Sword": ("Steel Sword", "钢剑", False),
        "US": ("US", "美国", True),
        "i": ("i", "i", False),
        "s": ("s", "s", False),
        "k": ("k", "k", False),
        "巨龙": ("Dragon", "龙", False),
    }
    matcher = MandatoryTermMatcher(terms)
    assert matcher.match(text) == match_required_terms(text, terms)
    if text == "Iron Sword and sword":
        assert matcher.match(text) == {"Steel Sword": "钢剑", "Sword": "剑"}
    if text == "İ ı ſ K":
        assert matcher.match(text) == {"i": "i", "s": "s", "k": "k"}


def test_reusable_term_view_retains_primary_over_alias_and_first_alias_precedence():
    entries = [
        TermEntry("First", "一", "test", variants=["Shared", "Other"]),
        TermEntry("Second", "二", "test", variants=["Shared", "Other"]),
        TermEntry("Shared", "主词", "test"),
    ]
    matcher = MandatoryTermMatcher.from_terms(entries)
    assert matcher.match("Shared Other") == {"Shared": "主词", "First": "一"}
