from __future__ import annotations

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.llm_arbiter import ArbiterDecision, ArbitrationContext, LLMArbiter
from transbridge.ai_translator.post_processor.llm_refiner import RefineResult
from transbridge.ai_translator.post_processor.polisher import LLMPolisher, PolishResult
from transbridge.ai_translator.post_processor.post_processor import PostProcessor, PostProcessorConfig
from transbridge.ai_translator.post_processor.proofread_pipeline import ProofreadPipeline
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile
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
    pipeline = ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", LLMConfig()))

    results = pipeline.process([entry])

    assert observed_polish_inputs == ["不要打开大门。"]
    assert entry.translation == "打开大门。"
    assert results[entry.id].polished_translation == "别打开那扇门。"
    assert results[entry.id].verdict == "pass"
    assert results[entry.id].accepted is True
    assert results[entry.id].issues[0].entry_id == entry.id


def test_polish_preset_defaults_to_full_proofreading_but_saved_values_win() -> None:
    from transbridge.application.translation.ai_execution_profile import (
        apply_profile_settings,
        ensure_workflow_profiles,
        store_profile_settings,
    )

    config = LLMConfig(pp_enable_polish=False)
    profiles = ensure_workflow_profiles(config)

    assert profiles["translate"]["pp_enable_polish"] is False
    assert profiles["polish"]["pp_enable_polish"] is True
    apply_profile_settings(config, "polish")
    config.pp_enable_refinement = False
    store_profile_settings(config, "polish")
    apply_profile_settings(config, "translate")
    assert config.pp_enable_refinement is True
    apply_profile_settings(config, "polish")
    assert config.pp_enable_refinement is False


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
    pipeline = ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", LLMConfig()))

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
    result = ProofreadPipeline(processor, AiExecutionProfile.from_config("polish", LLMConfig())).process([_entry()])[
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
        '[{"entry_id":"legacy-id","verdict":"pass","reason":"ok","issues":[]}]',
    )
    assert [issue.entry_id for issue in issues] == ["second-id"]

    refiner = object.__new__(LLMRefiner)
    refined = refiner._parse_batch_refinement_response(
        entries,
        '[{"entry_id":"legacy-id","refined_translation":"不要打开。","confidence":0.9}]',
    )
    assert set(refined) == {"legacy-id", "second-id"}
    assert refined["second-id"].confidence == 0.0

    polisher = object.__new__(LLMPolisher)
    polished = polisher._parse_batch_polish_response(
        entries,
        '[{"entry_id":"legacy-id","polished_translation":"别打开。","confidence":0.9}]',
    )
    assert set(polished) == {"legacy-id", "second-id"}
    assert polished["second-id"].confidence == 0.0

    arbiter = object.__new__(LLMArbiter)
    arbiter._strict_mode = False
    contexts = [ArbitrationContext(entry=entry) for entry in entries]
    decisions = arbiter._parse_batch_arbitration_response(
        contexts,
        '[{"entry_id":"legacy-id","verdict":"pass","reason":"ok","confidence":0.9}]',
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
    config = LLMConfig(model="", workflow_profiles={"polish": {"pp_enable_polish": False}})

    config.save_to_file(repository=repository, credential_store=store)

    reloaded = LLMConfig.load_from_file(repository=repository, credential_store=store)
    assert reloaded.workflow_profiles["polish"]["pp_enable_polish"] is False
