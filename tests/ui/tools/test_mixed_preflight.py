from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.translation.ai_execution_profile import AiExecutionProfile, capture_profile_settings
from transbridge.config.llm import EmbeddingConfig, LLMConfig
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.paratranz import config_manager as paratranz_config
from transbridge.paratranz.config_manager import ActionRule
from transbridge.ui.tools.ai_translator import (
    _mixed_worker as mixed_module,
    ai_translator_window as window_module,
    config_presenter as config_module,
)
from transbridge.ui.tools.ai_translator._window_actions import require_ready
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.result_actions import FailedSubsetRetryFactory
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.run_spec import AiPreflightCode, preflight_ai_run
from transbridge.ui.tools.ai_translator.scope_presenter import ScopePresenter


def _local_config() -> LLMConfig:
    return LLMConfig(
        pp_strategy="strict",
        pp_enable_quality_gate=False,
        pp_enable_refinement=False,
        pp_enable_polish=False,
        pp_enable_arbitration=False,
        action_rules=[ActionRule(status_filter={1}, action="polish")],
    )


def _entries() -> list[TranslationEntry]:
    return [
        TranslationEntry("valid", "valid", "Hello{0}", "translated{0}", 1, ""),
        TranslationEntry("invalid", "invalid", "Hello{0}", "translated", 1, ""),
    ]


def _scope(config: LLMConfig, entries: list[TranslationEntry]):
    presenter = ScopePresenter(lambda: entries, lambda: {}, lambda _: "", MagicMock())
    return presenter.partition_mixed(config.action_rules, entries)


def _preflight(config: LLMConfig):
    scope = _scope(config, _entries())
    return preflight_ai_run(
        "mixed",
        config,
        (*scope.translate_entries, *scope.polish_entries),
        esp_path=None,
        mixed_has_translation=bool(scope.translate_entries),
        dependency_available=lambda _: False,
    )


def test_mixed_local_checks_run_without_llm_credentials_or_translation_dependencies(monkeypatch) -> None:
    config = _local_config()
    config.embedding = EmbeddingConfig(mode="local")
    entries = _entries()
    before = deepcopy(entries)
    scope = _scope(config, entries)
    monkeypatch.setattr(mixed_module, "WorkflowLogStore", lambda *_args, **_kwargs: MagicMock())
    from transbridge.infra import llm_client, token_counting

    monkeypatch.setattr(llm_client, "create_llm_client", lambda *_: pytest.fail("local checks requested an LLM"))
    monkeypatch.setattr(token_counting, "tiktoken", None)
    monkeypatch.setattr(paratranz_config, "apply_rules", lambda *_: pytest.fail("preflight rescanned rules"))

    preflight = preflight_ai_run(
        "mixed",
        config,
        entries,
        esp_path=None,
        mixed_has_translation=bool(scope.translate_entries),
        dependency_available=lambda _: False,
    )
    worker = mixed_module._MixedWorker(config, list(scope.translate_entries), list(scope.polish_entries))
    result = worker._run_serial()

    assert preflight.ready
    assert not AiExecutionProfile.from_config("mixed", config).requires_llm
    assert set(result) == {"polish"}
    assert result["polish"].candidates["valid"].accepted
    assert not result["polish"].candidates["invalid"].accepted
    assert result["polish"].candidates["invalid"].issues
    assert entries == before


def test_mixed_translation_still_requires_credentials_source_and_translation_dependencies() -> None:
    config = _local_config()
    config.action_rules = [ActionRule(action="translate")]

    result = _preflight(config)

    assert {issue.code for issue in result.issues} == {
        AiPreflightCode.MISSING_API_KEY,
        AiPreflightCode.MISSING_MODEL,
        AiPreflightCode.MISSING_SOURCE,
        AiPreflightCode.MISSING_DEPENDENCY,
    }


def test_mixed_llm_polish_keeps_credentials_check_but_does_not_require_translation_dependencies() -> None:
    config = _local_config()
    config.pp_enable_quality_gate = True

    result = _preflight(config)

    assert {issue.code for issue in result.issues} == {
        AiPreflightCode.MISSING_API_KEY,
        AiPreflightCode.MISSING_MODEL,
    }


@pytest.mark.parametrize("empty_rules", [False, True])
def test_mixed_noop_remains_blocked(empty_rules: bool) -> None:
    config = _local_config()
    if empty_rules:
        config.action_rules = []
    else:
        config.enable_post_process = False

    result = _preflight(config)

    expected = AiPreflightCode.EMPTY_SCOPE if empty_rules else AiPreflightCode.EMPTY_WORKFLOW
    assert [issue.code for issue in result.issues] == [expected]


def test_mixed_quick_run_and_start_gate_follow_current_rule_assignment(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    config = _local_config()
    config.workflow_profiles = {"mixed": capture_profile_settings(config)}
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: deepcopy(config))
    monkeypatch.setattr(config_module.LLMConfig, "save_to_file", lambda *_: None)
    entries = _entries()
    context = SimpleNamespace(
        collection=TranslationEntryCollection(entries),
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: (), locate_entry=lambda _: None)
    window = AITranslatorWindow(context, workbench)
    try:
        window._view.controls.mode_mixed.setChecked(True)
        partition = window._scope_presenter.partition_mixed
        partition_calls = []

        def partition_once(rules, values):
            partition_calls.append(True)
            return partition(rules, values)

        monkeypatch.setattr(window._scope_presenter, "partition_mixed", partition_once)
        window.update_quick_run()

        assert len(partition_calls) == 1
        assert window._view.controls.start_btn.isEnabled()
        assert require_ready(window, "mixed", window._config_presenter.build(), entries, mixed_has_translation=False)
        started = []
        monkeypatch.setattr(window_module, "try_begin_run", lambda *_args, **_kwargs: object())
        monkeypatch.setattr(
            window_module,
            "start_versioned_mixed",
            lambda _window, _request, translated, polished: started.append((translated, polished)),
        )
        monkeypatch.setattr(window_module.QMessageBox, "warning", lambda *_: pytest.fail("local start was blocked"))
        window._on_mixed_start()

        assert started == [([], entries)]

        window._view.controls.rule_editor.set_rules([ActionRule(action="translate")])
        window.update_quick_run()

        assert not window._view.controls.start_btn.isEnabled()
        assert "API Key" in window._view.controls.preflight_label.text()
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize(
    ("mode", "has_translation"),
    [("mixed", False), ("mixed", True), ("mixed", None), ("polish", None), ("translate", None)],
)
def test_retry_uses_existing_stage_or_mode_without_scanning_rules(monkeypatch, mode, has_translation) -> None:
    config = _local_config()
    entries = _entries()
    controller = RunController(owner_id="mixed-retry")
    previous = controller.begin(mode, config, entries).spec
    controller.finish(previous.run_id)
    monkeypatch.setattr(paratranz_config, "apply_rules", lambda *_: pytest.fail("retry rescanned rules"))
    stage = {} if has_translation is None else {"mixed_has_translation": has_translation}

    prepared = FailedSubsetRetryFactory().prepare(
        previous=previous,
        failed_entry_keys=("invalid",),
        current_entries=entries,
        current_config=config,
        esp_path=None,
        controller=controller,
        **stage,
    )

    if mode == "polish" or has_translation is False:
        assert prepared.preflight.ready
        assert prepared.request is not None
        assert prepared.request.run_id != previous.run_id
        assert [entry.id for entry in prepared.request.entries] == ["invalid"]
    else:
        assert prepared.request is None
        assert {AiPreflightCode.MISSING_API_KEY, AiPreflightCode.MISSING_MODEL} <= {
            issue.code for issue in prepared.preflight.issues
        }
    controller.close()
