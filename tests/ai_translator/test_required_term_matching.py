from types import SimpleNamespace

from transbridge.ai_translator.required_term_matching import match_required_terms
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
