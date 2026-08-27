from __future__ import annotations

import json

import pytest

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.llm_arbiter import ArbiterDecision, ArbitrationContext, LLMArbiter
from transbridge.ai_translator.post_processor.llm_refiner import RefineResult
from transbridge.ai_translator.post_processor.polisher import LLMPolisher, PolishResult
from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
from transbridge.application.translation.token_batching import ContentTokenCount
from transbridge.config.llm import LLMConfig
from transbridge.converter.translation_entry import TranslationEntry


def _entry() -> TranslationEntry:
    return TranslationEntry(
        id="legacy-id",
        key="stable-key",
        original="Do not open the gate.",
        translation="打开大门。",
        stage=1,
        context="DIAL:FULL",
    )


class _CharacterCounter:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def count(self, text: str) -> ContentTokenCount:
        self.seen.append(text)
        return ContentTokenCount(len(text), False, "characters")


def test_proofread_pipeline_repairs_before_polish_without_mutating_formal_entry() -> None:
    entry = _entry()
    observed_polish_inputs: list[str] = []

    class Checker:
        def check(self, checked_entry):
            return [
                PostProcessIssue(
                    entry_id=checked_entry.key,
                    issue_type=PostProcessIssue.LOW_QUALITY,
                    severity="error",
                    message="否定含义丢失",
                    original=checked_entry.original,
                    translation=checked_entry.translation,
                )
            ]

    class Refiner:
        def refine_batch(self, entries, issues_map):
            assert issues_map["legacy-id"][0].message == "否定含义丢失"
            return {
                "legacy-id": RefineResult(
                    entry_id="legacy-id",
                    original_translation=entries[0].translation,
                    refined_translation="不要打开大门。",
                    confidence=0.95,
                )
            }

    class Polisher:
        def polish_batch(self, entries):
            observed_polish_inputs.extend(item.translation for item in entries)
            return {
                "legacy-id": PolishResult(
                    entry_id="legacy-id",
                    original_translation=entries[0].translation,
                    polished_translation="别打开那扇门。",
                    confidence=0.9,
                )
            }

    class Arbiter:
        def arbitrate_batch(self, contexts):
            return {
                context.entry.key: ArbiterDecision(
                    entry_id=context.entry.key,
                    verdict="pass",
                    reason="否定含义已恢复",
                    confidence=0.96,
                    suggested_action="接受",
                )
                for context in contexts
            }

    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=False,
            enable_refinement=True,
            enable_polish=True,
            enable_llm_arbitration=True,
        )
    )
    processor.register_checker(Checker())
    processor._refiner = Refiner()
    processor._polisher = Polisher()
    processor._arbiter = Arbiter()
    strict_config = LLMConfig()
    strict_config.pp_strategy = "strict"
    pipeline = ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", strict_config))

    results = pipeline.process([entry])

    assert observed_polish_inputs == ["不要打开大门。"]
    assert entry.translation == "打开大门。"
    assert results[entry.id].polished_translation == "别打开那扇门。"
    assert results[entry.id].verdict == "pass"
    assert results[entry.id].accepted is True
    assert results[entry.id].issues[0].entry_id == entry.id


@pytest.mark.parametrize(
    ("max_tokens", "max_items", "expected_sizes"),
    [
        (100, 2, [2, 2, 1]),
        (5, 10, [1, 1, 1, 1, 1]),
    ],
)
def test_quality_gate_batches_obey_token_and_item_limits(max_tokens, max_items, expected_sizes) -> None:
    from transbridge.ai_translator.post_processor.quality_gate import QualityGateChecker

    entries = [TranslationEntry(str(index), str(index), "aa", "bb", 1, "c") for index in range(5)]
    observed: list[list[str]] = []
    quality_gate = object.__new__(QualityGateChecker)
    quality_gate.check_batch = lambda batch: observed.append([entry.id for entry in batch]) or []
    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=True,
            quality_gate_batch_size=max_items,
            enable_refinement=False,
            enable_polish=False,
            enable_llm_arbitration=False,
            max_tokens_per_batch=max_tokens,
        ),
        token_counter=_CharacterCounter(),
    )
    processor.register_checker(quality_gate)

    processor.process_entries(entries, max_workers=1, apply_changes=False)

    assert [len(batch) for batch in observed] == expected_sizes


def test_refine_and_polish_replan_from_stage_specific_business_content() -> None:
    entries = [TranslationEntry(str(index), str(index), "a", "b", 1, "") for index in range(2)]
    counter = _CharacterCounter()
    refine_batches: list[list[str]] = []
    polish_inputs: list[str] = []

    class Checker:
        def check(self, entry):
            return [
                PostProcessIssue(
                    entry_id=entry.id,
                    issue_type="x",
                    severity="warning",
                    message="m",
                    original=entry.original,
                    translation=entry.translation,
                )
            ]

    class Refiner:
        def refine_batch(self, batch, issues_map):
            refine_batches.append([entry.id for entry in batch])
            return {
                entry.id: RefineResult(
                    entry_id=entry.id,
                    original_translation=entry.translation,
                    refined_translation="R" * 9,
                    confidence=0.95,
                )
                for entry in batch
            }

    class Polisher:
        def polish_batch(self, batch):
            polish_inputs.extend(entry.translation for entry in batch)
            return {
                entry.id: PolishResult(
                    entry_id=entry.id,
                    original_translation=entry.translation,
                    polished_translation=f"{entry.translation}!",
                    confidence=0.9,
                )
                for entry in batch
            }

    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=False,
            enable_refinement=True,
            refinement_batch_size=10,
            enable_polish=True,
            polish_batch_size=10,
            enable_llm_arbitration=False,
            max_tokens_per_batch=11,
        ),
        token_counter=counter,
    )
    processor.register_checker(Checker())
    processor._refiner = Refiner()
    processor._polisher = Polisher()

    processor.process_entries(entries, max_workers=1, apply_changes=False)

    assert [len(batch) for batch in refine_batches] == [1, 1]
    assert polish_inputs == ["R" * 9, "R" * 9]
    assert "R" * 9 in counter.seen


def test_arbitration_filters_quick_decisions_before_token_batching() -> None:
    huge = TranslationEntry("quick", "quick", "x" * 100, "old", 1, "")
    needs_llm = [
        TranslationEntry("llm-a", "llm-a", "a", "b", 1, ""),
        TranslationEntry("llm-b", "llm-b", "c", "d", 1, ""),
    ]
    observed: list[list[str]] = []

    class Arbiter:
        def _quick_decide(self, context):
            if context.entry.id != "quick":
                return None
            return ArbiterDecision("quick", "pass", "规则通过", 1.0, "接受")

        def arbitrate_batch(self, contexts):
            observed.append([context.entry.id for context in contexts])
            return {
                context.entry.key: ArbiterDecision(context.entry.key, "pass", "模型通过", 0.9, "接受")
                for context in contexts
            }

    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=False,
            enable_refinement=False,
            enable_polish=False,
            enable_llm_arbitration=True,
            max_tokens_per_batch=3,
        ),
        token_counter=_CharacterCounter(),
    )
    processor._arbiter = Arbiter()

    result = processor.process_entries([huge, *needs_llm], max_workers=1, apply_changes=False)

    assert observed == [["llm-a"], ["llm-b"]]
    assert set(result.decisions) == {"quick", "llm-a", "llm-b"}


def test_oversized_quality_gate_item_fails_before_llm_call() -> None:
    from transbridge.ai_translator.post_processor.quality_gate import QualityGateChecker

    calls: list[list] = []
    quality_gate = object.__new__(QualityGateChecker)
    quality_gate.check_batch = lambda batch: calls.append(batch) or []
    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=True,
            enable_refinement=False,
            enable_polish=False,
            enable_llm_arbitration=False,
            max_tokens_per_batch=4,
        ),
        token_counter=_CharacterCounter(),
    )
    processor.register_checker(quality_gate)

    with pytest.raises(ValueError, match="质量检测阶段.*Token"):
        processor.process_entries(
            [TranslationEntry("one", "one", "aa", "bb", 1, "c")],
            max_workers=1,
            apply_changes=False,
        )

    assert calls == []


def test_postprocessor_config_forwards_model_and_content_token_limit() -> None:
    config = PostProcessorConfig.from_llm_config(LLMConfig(model="fixture-model", max_tokens_per_batch=321))

    assert config.model == "fixture-model"
    assert config.max_tokens_per_batch == 321

    profile = AiExecutionProfile.from_config("polish", LLMConfig())
    pipeline = ProofreadPipeline.create(
        profile=profile,
        llm_client=None,
        model="profile-model",
        max_tokens_per_batch=654,
        token_counter=_CharacterCounter(),
    )
    assert pipeline._processor._config.model == "profile-model"
    assert pipeline._processor._config.max_tokens_per_batch == 654


def test_proofread_pipeline_marks_only_final_technically_invalid_entries_as_failed() -> None:
    import json

    valid = _entry()
    missing = TranslationEntry(
        id="missing-id",
        key="missing-key",
        original="Keep %s",
        translation="保留 %s",
        stage=1,
        context="DIAL:FULL",
    )

    class Client:
        @staticmethod
        def chat_prepared(messages_factory, max_tokens=0):
            payload = json.loads(messages_factory()[1]["content"])
            first = payload["entries"][0]
            return json.dumps(
                {"results": [{"entry_key": first["entry_key"], "final_translation": "不要打开大门。"}]},
                ensure_ascii=False,
            )

    profile = AiExecutionProfile.from_config("polish", LLMConfig(pp_strategy="proofread"))
    results = ProofreadPipeline.create(
        profile=profile,
        llm_client=Client(),
        max_tokens_per_batch=10_000,
    ).process([valid, missing])

    assert results[valid.id].verdict == "pass"
    assert results[valid.id].polished_translation == "不要打开大门。"
    assert results[missing.id].verdict == "failed"
    assert results[missing.id].polished_translation == missing.translation
    assert results[missing.id].needs_arbitration is False


def test_factory_presets_default_to_proofread_and_saved_values_win() -> None:
    from transbridge.application.translation.ai_execution_profile import (
        apply_profile_settings,
        ensure_workflow_profiles,
        store_profile_settings,
    )

    config = LLMConfig(pp_enable_polish=False)
    profiles = ensure_workflow_profiles(config)

    assert {profiles[name]["pp_strategy"] for name in ("translate", "polish", "mixed")} == {"proofread"}
    assert profiles["translate"]["pp_enable_polish"] is False
    assert profiles["polish"]["pp_enable_polish"] is True
    apply_profile_settings(config, "polish")
    config.pp_enable_refinement = False
    store_profile_settings(config, "polish")
    apply_profile_settings(config, "translate")
    assert config.pp_enable_refinement is True
    apply_profile_settings(config, "polish")
    assert config.pp_enable_refinement is False


def test_legacy_combined_config_normalizes_to_proofread() -> None:
    profile = AiExecutionProfile.from_config("polish", LLMConfig(pp_strategy="combined"))

    assert profile.postprocess_strategy == "proofread"
    assert profile.enable_proofread is True
    assert profile.enable_combined_proofread is True


def test_legacy_strategy_is_canonicalized_at_profile_and_config_save_boundaries() -> None:
    from transbridge.application.translation.ai_execution_profile import capture_profile_settings

    config = LLMConfig(
        pp_strategy="combined",
        workflow_profiles={"polish": {"pp_strategy": "combined", "pp_enable_polish": True}},
    )

    assert capture_profile_settings(config)["pp_strategy"] == "proofread"
    raw = config._serialize_workflow_profiles()
    assert json.loads(raw)["polish"]["pp_strategy"] == "proofread"
    assert config.workflow_profiles["polish"]["pp_strategy"] == "combined"
    assert LLMConfig._load_workflow_profiles(raw)["polish"]["pp_strategy"] == "proofread"


def test_saved_legacy_profile_without_strategy_keeps_strict_stage_semantics() -> None:
    from transbridge.application.translation.ai_execution_profile import ensure_workflow_profiles

    config = LLMConfig(
        workflow_profiles={
            "polish": {
                "pp_enable_quality_gate": True,
                "pp_enable_refinement": True,
                "pp_enable_polish": True,
                "pp_enable_arbitration": True,
            }
        }
    )

    profiles = ensure_workflow_profiles(config)

    assert profiles["polish"]["pp_strategy"] == "strict"


def test_workflow_profiles_json_round_trip_and_rejects_invalid_root() -> None:
    config = LLMConfig(workflow_profiles={"polish": {"pp_enable_polish": True}})

    raw = config._serialize_workflow_profiles()

    assert LLMConfig._load_workflow_profiles(raw) == config.workflow_profiles
    assert LLMConfig._load_workflow_profiles("[]") == {}
    assert LLMConfig._load_workflow_profiles("not-json") == {}


def test_config_presenter_switches_independent_in_memory_presets(monkeypatch) -> None:
    from transbridge.ui.tools.ai_translator import config_presenter as presenter_module
    from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter

    persisted = LLMConfig()

    class View:
        values: dict[str, object] = {}

        def render_config(self, config) -> None:
            self.values = {
                "pp_enable_refinement": config.pp_enable_refinement,
                "pp_enable_polish": config.pp_enable_polish,
            }

        def update_config(self, config):
            for name, value in self.values.items():
                setattr(config, name, value)
            return config

    monkeypatch.setattr(presenter_module.LLMConfig, "load_from_file", lambda: persisted)
    view = View()
    presenter = ConfigPresenter(view)
    presenter.load()
    view.values["pp_enable_refinement"] = False

    presenter.switch_preset("polish")

    assert view.values == {"pp_enable_refinement": True, "pp_enable_polish": True}
    view.values["pp_enable_polish"] = False
    presenter.switch_preset("translate")
    assert view.values == {"pp_enable_refinement": False, "pp_enable_polish": False}


def test_polish_preflight_rejects_a_noop_workflow() -> None:
    from transbridge.ui.tools.ai_translator.run_spec import AiPreflightCode, preflight_ai_run

    config = LLMConfig(
        api_key="key",
        model="model",
        enable_post_process=False,
    )

    result = preflight_ai_run("polish", config, [_entry()], esp_path=None)

    assert AiPreflightCode.EMPTY_WORKFLOW in {issue.code for issue in result.issues}


def test_failed_polish_stage_never_becomes_an_accepted_candidate() -> None:
    class FailedPolisher:
        def polish_batch(self, entries):
            return {
                entries[0].id: PolishResult(
                    entry_id=entries[0].id,
                    original_translation=entries[0].translation,
                    polished_translation=entries[0].translation,
                    confidence=0.0,
                    note="LLM润色失败: timeout",
                )
            }

    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=False,
            enable_refinement=False,
            enable_polish=True,
            enable_llm_arbitration=False,
        )
    )
    processor._polisher = FailedPolisher()
    strict_config = LLMConfig(pp_strategy="strict")
    pipeline = ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", strict_config))

    result = pipeline.process([_entry()])["legacy-id"]

    assert result.verdict == "pending"
    assert result.confidence == 0.0
    assert result.accepted is False


def test_missing_arbitration_decision_is_never_accepted() -> None:
    class Polisher:
        def polish_batch(self, entries):
            return {
                entries[0].id: PolishResult(
                    entry_id=entries[0].id,
                    original_translation=entries[0].translation,
                    polished_translation="可能有问题的候选",
                    confidence=0.9,
                )
            }

    class MissingArbiter:
        def arbitrate_batch(self, contexts):
            return {}

    processor = PostProcessor(
        PostProcessorConfig(
            enable_consistency_check=False,
            enable_format_validation=False,
            enable_quality_gate=False,
            enable_refinement=False,
            enable_polish=True,
            enable_llm_arbitration=True,
        )
    )
    processor._polisher = Polisher()
    processor._arbiter = MissingArbiter()
    strict_config = LLMConfig(pp_strategy="strict")
    result = ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", strict_config)).process([_entry()])[
        "legacy-id"
    ]

    assert result.verdict == "pending"
    assert result.confidence == 0.0
    assert result.accepted is False


def test_batch_parsers_accept_id_aliases_and_mark_missing_items() -> None:
    from transbridge.ai_translator.post_processor.llm_refiner import LLMRefiner
    from transbridge.ai_translator.post_processor.quality_gate import QualityGateChecker

    first = _entry()
    second = TranslationEntry(
        id="second-id",
        key="second-key",
        original="Close it.",
        translation="关上。",
        stage=1,
        context="DIAL:FULL",
    )
    entries = [first, second]

    quality = object.__new__(QualityGateChecker)
    issues = quality._parse_batch_response(
        entries,
        '{"results":[{"entry_id":"legacy-id","verdict":"pass","reason":"ok","issues":[]}]}',
    )
    assert [issue.entry_id for issue in issues] == ["second-id"]

    refiner = object.__new__(LLMRefiner)
    refined = refiner._parse_batch_refinement_response(
        entries,
        '{"results":[{"entry_id":"legacy-id","refined_translation":"不要打开。","confidence":0.9}]}',
    )
    assert set(refined) == {"legacy-id", "second-id"}
    assert refined["second-id"].confidence == 0.0

    polisher = object.__new__(LLMPolisher)
    polished = polisher._parse_batch_polish_response(
        entries,
        '{"results":[{"entry_id":"legacy-id","polished_translation":"别打开。","confidence":0.9}]}',
    )
    assert set(polished) == {"legacy-id", "second-id"}
    assert polished["second-id"].confidence == 0.0

    arbiter = object.__new__(LLMArbiter)
    arbiter._strict_mode = False
    contexts = [ArbitrationContext(entry=entry) for entry in entries]
    decisions = arbiter._parse_batch_arbitration_response(
        contexts,
        '{"results":[{"entry_id":"legacy-id","verdict":"pass","reason":"ok","confidence":0.9}]}',
    )
    assert set(decisions) == {"legacy-id", "second-id"}
    assert decisions["second-id"].verdict == "pending"


def test_execution_profile_forwards_game_and_target_language() -> None:
    profile = AiExecutionProfile.from_config(
        "polish",
        LLMConfig(
            game_profile="fallout4",
            target_lang="ja_JP",
            pp_enable_quality_gate=False,
            pp_enable_refinement=False,
            pp_enable_polish=False,
            pp_enable_arbitration=False,
        ),
    )

    pipeline = ProofreadPipeline.create(profile=profile, llm_client=None)

    assert pipeline._processor._config.game_profile == "fallout4"
    assert pipeline._processor._config.target_lang == "ja_JP"


def test_high_confidence_refine_does_not_bypass_final_polish_arbitration() -> None:
    from transbridge.ai_translator.post_processor.llm_refiner import FixApplied

    entry = _entry()
    issue = PostProcessIssue(
        entry_id=entry.id,
        issue_type="wording",
        severity="warning",
        message="措辞问题",
        original=entry.original,
        translation=entry.translation,
    )
    arbiter = object.__new__(LLMArbiter)
    arbiter._strict_mode = False
    decision = arbiter._quick_decide(
        ArbitrationContext(
            entry=entry,
            original_issues=[issue],
            refine_result=RefineResult(
                entry_id=entry.id,
                original_translation=entry.translation,
                refined_translation="不要打开大门。",
                fixes_applied=[FixApplied("wording", "措辞问题", "已修复")],
                confidence=0.99,
            ),
            polish_result=PolishResult(
                entry_id=entry.id,
                original_translation="不要打开大门。",
                polished_translation="打开大门。",
                confidence=0.9,
            ),
        )
    )

    assert decision is None


def test_local_only_proofread_does_not_require_llm_credentials() -> None:
    from transbridge.ui.tools.ai_translator.run_spec import preflight_ai_run

    result = preflight_ai_run(
        "polish",
        LLMConfig(
            api_key="",
            model="",
            pp_strategy="strict",
            pp_enable_consistency_check=True,
            pp_enable_format_validation=True,
            pp_enable_quality_gate=False,
            pp_enable_refinement=False,
            pp_enable_polish=False,
            pp_enable_arbitration=False,
        ),
        [_entry()],
        esp_path=None,
    )

    assert result.ready


def test_real_repository_profiles_remain_independent_and_execution_copy_is_detached(tmp_path, monkeypatch) -> None:
    from copy import deepcopy
    from dataclasses import dataclass, field

    from transbridge.config.paratranz_credentials import CredentialRef, SecretStoreCapability, SecretValue
    from transbridge.config.repository import ConfigRepository
    from transbridge.ui.tools.ai_translator import config_presenter as presenter_module
    from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
    from transbridge.ui.tools.ai_translator.run_spec import FrozenExecutionConfig

    @dataclass
    class Store:
        values: dict[str, SecretValue] = field(default_factory=dict)

        @property
        def capability(self):
            return SecretStoreCapability(True, True)

        def get(self, reference: CredentialRef):
            return self.values.get(reference.target_name)

        def set(self, reference: CredentialRef, value: SecretValue):
            self.values[reference.target_name] = value

        def delete(self, reference: CredentialRef):
            self.values.pop(reference.target_name, None)

    store = Store()
    path = tmp_path / "transbridge.ini"
    repository = ConfigRepository(path, legacy_path=path, credential_store=store)
    initial = LLMConfig(model="model")
    initial.workflow_profiles = {
        "translate": {"pp_enable_polish": False, "pp_quality_gate_batch_size": 10},
        "polish": {"pp_enable_polish": True, "pp_quality_gate_batch_size": 99},
    }
    initial.save_to_file(repository=repository, credential_store=store)

    load_from_repository = LLMConfig.load_from_file
    monkeypatch.setattr(
        presenter_module.LLMConfig,
        "load_from_file",
        lambda: load_from_repository(repository=repository, credential_store=store),
    )

    class View:
        values: dict[str, object] = {}

        def render_config(self, config):
            self.values = {"pp_enable_polish": config.pp_enable_polish}

        def update_config(self, config):
            for name, value in self.values.items():
                setattr(config, name, value)
            return config

    presenter = ConfigPresenter(View())
    presenter.load()
    presenter.switch_preset("polish")
    active = presenter.save()
    reloaded = load_from_repository(repository=repository, credential_store=store)

    assert active.pp_enable_polish is True
    assert active.pp_quality_gate_batch_size == 99
    assert reloaded.pp_enable_polish is False
    assert reloaded.workflow_profiles["polish"]["pp_quality_gate_batch_size"] == 99
    detached = FrozenExecutionConfig(reloaded).copy()
    assert detached.model == "model"
    assert detached._repository is None
    deepcopy(detached)


def test_config_digest_redacts_nested_credentials() -> None:
    from transbridge.ui.tools.ai_translator.run_spec import _config_digest

    first = LLMConfig(api_key="first", mcp_auth_token="first", model="model")
    first.embedding.api_key = "first"
    second = first.copy_for_execution()
    second.api_key = "second"
    second.mcp_auth_token = "second"
    second.embedding.api_key = "second"

    assert _config_digest(first) == _config_digest(second)


def test_profile_settings_can_persist_without_an_llm_endpoint(tmp_path) -> None:
    from dataclasses import dataclass

    from transbridge.config.paratranz_credentials import CredentialRef, SecretStoreCapability
    from transbridge.config.repository import ConfigRepository

    @dataclass
    class Store:
        @property
        def capability(self):
            return SecretStoreCapability(True, True)

        def get(self, reference: CredentialRef):
            return None

        def set(self, reference: CredentialRef, value):
            return None

        def delete(self, reference: CredentialRef):
            return None

    store = Store()
    path = tmp_path / "transbridge.ini"
    repository = ConfigRepository(path, legacy_path=path, credential_store=store)
    config = LLMConfig(
        model="",
        pp_strategy="combined",
        workflow_profiles={"polish": {"pp_strategy": "combined", "pp_enable_polish": False}},
    )

    config.save_to_file(repository=repository, credential_store=store)

    persisted = repository.load()
    assert persisted.value("llm", "pp_strategy") == "proofread"
    assert json.loads(persisted.value("llm", "workflow_profiles"))["polish"]["pp_strategy"] == "proofread"
    reloaded = LLMConfig.load_from_file(repository=repository, credential_store=store)
    assert reloaded.workflow_profiles["polish"]["pp_strategy"] == "proofread"
    assert reloaded.workflow_profiles["polish"]["pp_enable_polish"] is False
