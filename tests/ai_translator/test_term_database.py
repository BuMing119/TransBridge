"""逐条术语作用域测试（Story 16）。"""

from __future__ import annotations

from types import SimpleNamespace

from transbridge.ai_translator.term_database import TermDatabaseManager, TermEntry


def _entry(key: str, original: str):
    return SimpleNamespace(key=key, original=original)


def _manager(terms: list[TermEntry], vector_index=None) -> TermDatabaseManager:
    manager = object.__new__(TermDatabaseManager)
    manager._retrieval_enabled = True
    manager._vector_index = vector_index
    manager._effective_terms = lambda: terms
    return manager


def _term(
    term: str,
    translation: str,
    *,
    case_sensitive: bool = False,
    variants: list[str] | None = None,
) -> TermEntry:
    return TermEntry(
        term=term,
        translation=translation,
        source="fixture",
        case_sensitive=case_sensitive,
        variants=variants or [],
    )


def test_scoped_matches_preserve_flat_compatibility_and_explicit_ownership():
    manager = _manager([
        _term("Dragonborn", "龙裔"),
        _term("The Bannered Mare", "母马横幅旅店"),
        _term("Black Briar Lodge", "黑棘据点"),
        _term("Whiterun", "白漫城", variants=["White Run"]),
    ])
    entries = [
        _entry("a", "Speak to the Dragonborn."),
        _entry("b", "Visit Bannered Mare."),
        _entry("c", "Black Briar"),
        _entry("d", "White Run is nearby."),
        _entry("e", "Nothing relevant."),
    ]

    scoped = manager.match_terms_scoped(entries, enable_semantic=False)

    assert manager.match_terms_enhanced(entries, enable_semantic=False) == scoped.flat_terms
    assert scoped.terms_by_entry == {
        "a": {"Dragonborn": "龙裔"},
        "b": {"The Bannered Mare": "母马横幅旅店"},
        "c": {"Black Briar Lodge": "黑棘据点"},
        "d": {"Whiterun": "白漫城"},
        "e": {},
    }


def test_case_sensitive_term_only_binds_matching_case():
    manager = _manager([_term("Dovah", "龙语", case_sensitive=True)])
    entries = [_entry("upper", "Dovah speaks"), _entry("lower", "dovah speaks")]

    scoped = manager.match_terms_scoped(entries, enable_semantic=False)

    assert scoped.terms_by_entry["upper"] == {"Dovah": "龙语"}
    assert scoped.terms_by_entry["lower"] == {}


def test_in_flight_terms_keep_flat_compatibility_but_only_bind_related_entry():
    manager = _manager([])
    entries = [_entry("related", "Ask New Hero for help."), _entry("other", "Good morning.")]

    scoped = manager.match_terms_scoped(
        entries,
        enable_semantic=False,
        in_flight_terms={"New Hero": "新英雄", "Unused Name": "未使用名称"},
    )

    assert scoped.flat_terms == {"New Hero": "新英雄", "Unused Name": "未使用名称"}
    assert scoped.terms_by_entry == {
        "related": {"New Hero": "新英雄"},
        "other": {},
    }


def test_semantic_top_three_results_preserve_query_ownership_and_duplicate_originals():
    calls = []

    class VectorIndex:
        available = True

        def search_hybrid_batch(self, originals, top_k):
            calls.append((list(originals), top_k))
            return {
                "first query": [
                    SimpleNamespace(term="A", translation="甲"),
                    SimpleNamespace(term="B", translation="乙"),
                    SimpleNamespace(term="C", translation="丙"),
                ],
                "second query": [
                    SimpleNamespace(term="B", translation="乙"),
                    SimpleNamespace(term="D", translation="丁"),
                ],
            }

    manager = _manager([], VectorIndex())
    entries = [
        _entry("first-1", "first query"),
        _entry("first-2", "first query"),
        _entry("second", "second query"),
    ]

    scoped = manager.match_terms_scoped(entries, enable_semantic=True)

    assert calls == [(["first query", "first query", "second query"], 3)]
    assert scoped.flat_terms == {"A": "甲", "B": "乙", "C": "丙", "D": "丁"}
    assert scoped.terms_by_entry == {
        "first-1": {"A": "甲", "B": "乙", "C": "丙"},
        "first-2": {"A": "甲", "B": "乙", "C": "丙"},
        "second": {"B": "乙", "D": "丁"},
    }


def test_batch_limit_uses_existing_priority_and_filters_entry_bindings():
    manager = _manager([_term("Exact", "精确"), _term("Longer Forward", "正向")])
    entries = [_entry("exact", "Exact"), _entry("forward", "Use Longer Forward now")]

    scoped = manager.match_terms_scoped(entries, enable_semantic=False, max_terms=1)

    assert scoped.flat_terms == {"Exact": "精确"}
    assert scoped.terms_by_entry == {
        "exact": {"Exact": "精确"},
        "forward": {},
    }


def test_empty_entries_and_non_positive_limit_return_empty_scopes():
    manager = _manager([_term("A", "甲")])

    assert manager.match_terms_scoped([], enable_semantic=True).flat_terms == {}
    scoped = manager.match_terms_scoped([_entry("a", "A")], max_terms=0)
    assert scoped.flat_terms == {}
    assert scoped.terms_by_entry == {"a": {}}


def test_disabled_retrieval_returns_source_only_scope_without_scanning_terms():
    manager = _manager([_term("A", "甲")])
    manager._retrieval_enabled = False
    manager._effective_terms = lambda: (_ for _ in ()).throw(AssertionError("must not scan"))

    scoped = manager.match_terms_scoped([_entry("a", "A")])

    assert scoped.flat_terms == {}
    assert scoped.terms_by_entry == {"a": {}}
