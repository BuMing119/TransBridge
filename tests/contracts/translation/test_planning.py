from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.translation import (
    ActionPlanner,
    ActionRuleSpec,
    ContextPlanner,
    PlanningEntry,
    RetrievalStatus,
    TranslationAction,
    build_run_spec,
)


def _key(name: str) -> EntryKey:
    return EntryKey(SourceNamespace.legacy(), name)


def _entry(name: str, stage: int = 0, context: str = "NPC_:FULL") -> PlanningEntry:
    return PlanningEntry(_key(name), stage, f"original-{name}", context=context)


def test_action_plan_is_complete_disjoint_and_stage_policy_precedes_rules() -> None:
    entries = (
        _entry("a", 0),
        _entry("b", -1),
        _entry("c", 9),
        _entry("d", 1, "BOOK:DESC"),
    )
    rules = (
        ActionRuleSpec("later", 20, TranslationAction.POLISH),
        ActionRuleSpec("first", 10, TranslationAction.TRANSLATE, contexts=frozenset({"NPC_:FULL"})),
    )

    plan = ActionPlanner().plan(entries, rules)

    partitions = [set(plan.partition(action)) for action in TranslationAction]
    assert set().union(*partitions) == {entry.key for entry in entries}
    assert sum(len(partition) for partition in partitions) == len(entries)
    assert plan.partition(TranslationAction.TRANSLATE) == (_key("a"),)
    assert set(plan.partition(TranslationAction.SKIP)) == {_key("b"), _key("c")}
    assert plan.partition(TranslationAction.POLISH) == (_key("d"),)


def test_context_plan_has_no_omissions_duplicates_and_orders_quests() -> None:
    entries = (
        _entry("book", context="BOOK:FULL"),
        _entry("scroll", context="SCRL:FULL"),
        _entry("q1-1", context="INFO:NAM1|quest-1"),
        _entry("q2-1", context="DIAL:FULL|quest-2"),
        _entry("q1-2", context="INFO:NAM1|quest-1"),
        _entry("long", context="BOOK:DESC"),
        _entry("unknown", context="MODX:TEXT"),
    )
    actions = ActionPlanner().plan(entries, [ActionRuleSpec("all", 0, TranslationAction.TRANSLATE)])

    plan = ContextPlanner(max_chars=10_000).plan(entries, actions)

    assert len(plan.keys) == len(set(plan.keys)) == len(entries)
    assert set(plan.keys) == {entry.key for entry in entries}
    book_batch = next(batch for batch in plan.batches if batch.category == "书名")
    assert book_batch.keys == (_key("book"), _key("scroll"))
    quest1 = [batch for batch in plan.batches if batch.quest_id == "quest-1"]
    assert [batch.quest_sequence for batch in quest1] == [0]
    assert quest1[0].keys == (_key("q1-1"), _key("q1-2"))
    assert any("CONTEXT_FALLBACK_ROUND3" in item for item in plan.diagnostics)


@pytest.mark.parametrize(
    ("context", "round_number", "category"),
    [
        ("NPC_:FULL", 1, "人名"),
        ("SCRL:FULL", 1, "书名"),
        ("WEAP:FULL", 1, "物品"),
        ("SPEL:DESC", 1, "法术技能"),
        ("FURN:FULL", 1, "互动"),
        ("QUST:FULL", 1, "任务名"),
        ("INFO:NAM1|quest", 2, "对话"),
        ("DIAL:FULL|quest", 2, "对话"),
        ("QUST:NNAM", 3, "长文本"),
        ("SCRL:DESC", 3, "长文本"),
    ],
)
def test_context_catalog_has_explicit_rounds(context: str, round_number: int, category: str) -> None:
    entry = _entry("one", context=context)
    actions = ActionPlanner().plan([entry], [ActionRuleSpec("all", 0, TranslationAction.TRANSLATE)])
    plan = ContextPlanner().plan([entry], actions)
    assert not plan.diagnostics
    assert (plan.batches[0].round_number, plan.batches[0].category) == (
        round_number,
        category,
    )


def test_disabled_retrieval_does_not_call_loader_and_spec_is_frozen_hashable() -> None:
    calls = 0
    parameters = {"temperature": 0, "nested": {"b": 2, "a": 1}}

    def loader() -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return ("must-not-load",)

    spec = build_run_spec(
        run_id="run-1",
        config_revision=7,
        input_revision=11,
        source_locale="en",
        target_locale="zh_CN",
        prompt_profile="skyrim_se",
        provider="openai",
        base_url="https://example.test/v1",
        model="m1",
        parameters=parameters,
        retrieval_enabled=False,
        retrieval_loader=loader,
        scope=(_key("a"),),
    )

    assert calls == 0
    assert spec.retrieval.status is RetrievalStatus.DISABLED
    assert hash(spec)
    fingerprint = spec.fingerprint
    parameters["nested"]["a"] = 999
    assert fingerprint == spec.fingerprint
    with pytest.raises(FrozenInstanceError):
        spec.model = "changed"  # type: ignore[misc]


def test_enabled_retrieval_failure_is_explicitly_degraded() -> None:
    def fail() -> tuple[str, ...]:
        raise RuntimeError("backend unavailable")

    spec = build_run_spec(
        run_id="run-2",
        config_revision=1,
        input_revision=1,
        source_locale="en",
        target_locale="zh_CN",
        prompt_profile="skyrim_se",
        provider="openai",
        base_url="https://example.test/v1",
        model="m1",
        parameters={},
        retrieval_enabled=True,
        retrieval_loader=fail,
        scope=(_key("a"),),
    )
    assert spec.retrieval.status is RetrievalStatus.DEGRADED
    assert spec.retrieval.reason_code == "RETRIEVAL_MANIFEST_LOAD_FAILED"


def test_legacy_term_manager_disabled_path_loads_no_corpus_or_vector(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from transbridge.ai_translator import term_database
    from transbridge.config.llm import LLMConfig

    constructions = 0

    class CorpusSpy:
        def __init__(self, _path: str) -> None:
            nonlocal constructions
            constructions += 1

        def load(self) -> None:
            raise AssertionError("disabled retrieval must not load a corpus")

    monkeypatch.setattr(term_database, "DynamicTermDatabase", CorpusSpy)
    monkeypatch.setattr(LLMConfig, "get_ai_translator_dir", staticmethod(lambda _stem: str(tmp_path)))
    config = SimpleNamespace(
        retrieval_enabled=False,
        enable_semantic_match=True,
        embedding=SimpleNamespace(mode="api"),
        term_priority=["dynamic"],
    )

    manager = term_database.TermDatabaseManager(config, "fixture.esp")
    monkeypatch.setattr(
        manager,
        "_init_vector_index",
        lambda: (_ for _ in ()).throw(AssertionError("disabled retrieval initialized vector")),
    )

    assert manager.load_all() == {}
    assert constructions == 0


def test_auto_translator_passes_prompt_profile_and_target_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from transbridge.ai_translator import (
        batch_planner,
        noun_extractor,
        prompt_builder,
        term_database,
    )
    from transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig
    from transbridge.infra import llm_client

    captured: dict[str, str] = {}

    class PromptSpy:
        def __init__(self, game_profile: str, target_lang: str) -> None:
            captured.update(game_profile=game_profile, target_lang=target_lang)

    monkeypatch.setattr(prompt_builder, "PromptBuilder", PromptSpy)
    monkeypatch.setattr(llm_client, "create_llm_client", lambda _config: object())
    monkeypatch.setattr(term_database, "TermDatabaseManager", lambda **_kwargs: object())
    monkeypatch.setattr(noun_extractor, "NounExtractor", lambda _client, _builder: object())
    monkeypatch.setattr(batch_planner, "BatchPlanner", lambda **_kwargs: object())
    config = SimpleNamespace(
        game_profile="fallout4",
        target_lang="ja_JP",
        max_tokens_per_batch=500,
        max_concurrent=1,
    )

    AutoTranslator(TranslatorConfig(config, "fixture.esp"))

    assert captured == {"game_profile": "fallout4", "target_lang": "ja_JP"}
