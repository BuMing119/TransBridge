from __future__ import annotations

from dataclasses import dataclass

import pytest

from transbridge.ai_translator.batch_planner import BatchPlanner
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation.token_batching import (
    ContentTokenCount,
    StableContentBatcher,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra import token_counting


class CharacterCounter:
    def count(self, text: str) -> ContentTokenCount:
        return ContentTokenCount(len(text), False, "test-chars")


@dataclass(frozen=True)
class Item:
    key: EntryKey
    fields: tuple[str, ...]


def _key(value: str) -> EntryKey:
    return EntryKey(SourceNamespace.legacy(), value)


def _entry(value: str, text: str, context: str) -> TranslationEntry:
    return TranslationEntry(value, value, text, "", 0, context)


def test_stable_batcher_counts_only_projected_business_fields_and_keeps_order() -> None:
    items = [
        Item(_key("a"), ("ab", "译")),
        Item(_key("b"), ("\n", "")),
        Item(_key("c"), ("占位符 {name}\n第二行",)),
    ]

    plan = StableContentBatcher(CharacterCounter(), 4).plan(
        items,
        key=lambda item: item.key,
        content=lambda item: item.fields,
    )

    assert [tuple(item.key.local_key for item in batch.items) for batch in plan.batches] == [("a", "b"), ("c",)]
    assert plan.batches[0].content_tokens == 4
    assert plan.oversized == ()
    assert plan.batches[1].content_tokens == len("占位符 {name}\n第二行")


def test_empty_unicode_and_optional_item_limit_are_deterministic() -> None:
    items = [
        Item(_key("empty"), ("",)),
        Item(_key("cjk"), ("汉字",)),
        Item(_key("emoji"), ("🙂",)),
    ]
    batcher = StableContentBatcher(CharacterCounter(), 10, max_items=2)

    first = batcher.plan(items, key=lambda item: item.key, content=lambda item: item.fields)
    second = batcher.plan(items, key=lambda item: item.key, content=lambda item: item.fields)

    assert [len(batch.items) for batch in first.batches] == [2, 1]
    assert [batch.content_tokens for batch in first.batches] == [2, 1]
    assert [batch.fingerprint for batch in first.batches] == [batch.fingerprint for batch in second.batches]

    changed = batcher.plan(
        [Item(_key("empty"), ("",)), Item(_key("cjk"), ("字符",)), Item(_key("emoji"), ("🙂",))],
        key=lambda item: item.key,
        content=lambda item: item.fields,
    )
    assert first.batches[0].content_tokens == changed.batches[0].content_tokens
    assert first.batches[0].fingerprint != changed.batches[0].fingerprint


def test_unknown_model_fallback_is_offline_conservative_and_marked_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnknownTiktoken:
        def encoding_for_model(self, _model: str):
            raise KeyError("unknown")

        def get_encoding(self, _name: str):
            raise AssertionError("unknown-model fallback must not load an encoding")

    monkeypatch.setattr(token_counting, "tiktoken", UnknownTiktoken())
    token_counting._known_model_encoding.cache_clear()

    count = token_counting.TiktokenContentTokenCounter("compatible-vendor-model").count("汉字\n🙂")

    assert count.is_estimate is True
    assert count.encoding == "utf8-bytes-v1"
    assert count.tokens >= 4


def test_known_openai_model_uses_corresponding_tiktoken_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    class Encoding:
        name = "model-native"

        @staticmethod
        def encode(text: str) -> list[int]:
            return list(range(len(text.split())))

    class KnownTiktoken:
        @staticmethod
        def encoding_for_model(model: str):
            assert model == "gpt-test-known"
            return Encoding()

    monkeypatch.setattr(token_counting, "tiktoken", KnownTiktoken())
    token_counting._known_model_encoding.cache_clear()

    count = token_counting.TiktokenContentTokenCounter("gpt-test-known").count("one two three")

    assert count == ContentTokenCount(3, False, "model-native")


def test_batch_planner_boundaries_do_not_change_with_concurrency_and_preserve_context_barriers() -> None:
    entries = [
        _entry("npc-1", "aa", "NPC_:FULL"),
        _entry("npc-2", "bb", "NPC_:FULL"),
        _entry("quest-a-1", "ccc", "INFO:NAM1|quest-a"),
        _entry("quest-b", "dd", "DIAL:FULL|quest-b"),
        _entry("quest-a-2", "ee", "INFO:NAM1|quest-a"),
        _entry("long", "ffff", "BOOK:DESC"),
    ]
    planner = BatchPlanner(max_tokens_per_batch=4, token_counter=CharacterCounter())

    fingerprints_by_concurrency = []
    boundaries_by_concurrency = []
    for concurrency in (1, 5, 50):
        plan = planner.plan(entries, max_workers=concurrency)
        fingerprints_by_concurrency.append([batch.fingerprint for batch in plan.all_batches()])
        boundaries_by_concurrency.append([[entry.key for entry in batch.entries] for batch in plan.all_batches()])
        assert all(batch.content_tokens <= 4 for batch in plan.all_batches())
        assert list(plan.round2_by_quest()) == ["quest-a", "quest-b"]
        assert [[entry.key for entry in batch.entries] for batch in plan.round2_by_quest()["quest-a"]] == [
            ["quest-a-1"],
            ["quest-a-2"],
        ]

    assert fingerprints_by_concurrency[0] == fingerprints_by_concurrency[1] == fingerprints_by_concurrency[2]
    assert boundaries_by_concurrency[0] == boundaries_by_concurrency[1] == boundaries_by_concurrency[2]


def test_batch_planner_sends_single_entry_over_budget_alone() -> None:
    oversized = _entry("too-long", "x" * 11, "BOOK:DESC")

    plan = BatchPlanner(max_tokens_per_batch=10, token_counter=CharacterCounter()).plan([oversized])

    assert len(plan.all_batches()) == 1
    assert plan.all_batches()[0].entries == [oversized]
    assert plan.all_batches()[0].content_tokens == 11
    assert plan.oversized == []


def test_oversized_entries_remain_isolated_from_empty_and_small_neighbours() -> None:
    items = [Item(_key(str(i)), (value,)) for i, value in enumerate(("", "xxxxx", "", "xxxxxx", "a", "b"))]
    batcher = StableContentBatcher(CharacterCounter(), 2)
    plan = batcher.plan(items, key=lambda item: item.key, content=lambda item: item.fields)
    assert [tuple(item.key.local_key for item in batch.items) for batch in plan.batches] == [
        ("0",),
        ("1",),
        ("2",),
        ("3",),
        ("4", "5"),
    ]
    assert plan.items == tuple(items)
    assert all(len(batch.items) == 1 or batch.content_tokens <= 2 for batch in plan.batches)
