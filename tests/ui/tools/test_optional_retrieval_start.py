"""Optional semantic retrieval asks once and cannot alter saved preferences."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QMessageBox
import pytest

from transbridge.config.llm import EmbeddingConfig, LLMConfig
from transbridge.ui.tools.ai_translator import _window_actions, config_presenter
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.run_preparation import confirm_optional_retrieval
from transbridge.ui.tools.ai_translator.run_spec import _config_digest, preflight_ai_run
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency

_APP = QApplication.instance() or QApplication([])


def _config(mode="api"):
    return LLMConfig(
        api_key="llm-key",
        model="llm-model",
        embedding=EmbeddingConfig(mode=mode, provider="openai", model="", base_url=""),
    )


def _preflight(config, mode="translate", **kwargs):
    return preflight_ai_run(
        mode,
        config,
        [SimpleNamespace(id="one")],
        esp_path="source.esp",
        dependency_available=lambda _: True,
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["api", "local", "unknown"])
def test_unavailable_optional_retrieval_is_not_a_core_blocker(mode):
    result = _preflight(_config(mode))
    assert not result.ready
    assert result.optional_issues
    assert not result.blocking_issues
    assert result.blocking_reason is None


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Cancel, 0])
def test_cancel_or_close_keeps_original_config_and_does_not_start(answer, monkeypatch):
    config = _config()
    before = _config_digest(config)
    monkeypatch.setattr(QMessageBox, "exec", lambda _: answer)

    assert confirm_optional_retrieval(None, config, _preflight(config).optional_issues) is None
    assert _config_digest(config) == before


def test_ignore_creates_only_one_run_fallback_with_consistent_identity(monkeypatch):
    config = _config()
    before = _config_digest(config)
    dialogs = []

    def choose(dialog):
        dialogs.append(dialog)
        assert dialog.button(QMessageBox.StandardButton.Ignore).text() == "忽略并继续"
        assert dialog.button(QMessageBox.StandardButton.Cancel).text() == "取消"
        assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.Cancel
        return QMessageBox.StandardButton.Ignore

    monkeypatch.setattr(QMessageBox, "exec", choose)
    issues = _preflight(config).optional_issues
    execution = confirm_optional_retrieval(None, config, (*issues, *issues))
    assert len(dialogs) == 1
    assert execution is not config
    assert execution.retrieval_enabled
    assert not execution.enable_semantic_match
    assert execution.embedding.mode == "disabled"
    assert execution.embedding is not config.embedding
    assert _preflight(execution).ready
    assert _config_digest(config) == before

    controller = RunController()
    request = controller.begin("translate", execution, [SimpleNamespace(id="one")], esp_path="source.esp")
    assert request.spec.config_digest == _config_digest(execution)
    assert request.spec.config_digest != before
    assert not request.config.enable_semantic_match
    consistency = TaskConsistency(request, config_provider=lambda: config)
    consistency.require_current(request)
    controller.close()


def test_pure_polish_and_disabled_retrieval_need_no_confirmation(monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda _: pytest.fail("unexpected semantic retrieval prompt"))
    config = _config()
    for result in (_preflight(config, "polish"), _preflight(config, "mixed", mixed_has_translation=False)):
        assert confirm_optional_retrieval(None, config, result.optional_issues) is config
    config.retrieval_enabled = False
    assert confirm_optional_retrieval(None, config, _preflight(config).optional_issues) is config


def test_quick_run_leaves_start_enabled_for_optional_failure(monkeypatch):
    config = _config()
    monkeypatch.setattr(config_presenter.LLMConfig, "load_from_file", lambda: config)
    monkeypatch.setattr(
        _window_actions,
        "preflight_ai_run",
        lambda mode, cfg, entries, **kwargs: preflight_ai_run(
            mode, cfg, entries, dependency_available=lambda _: True, **kwargs
        ),
    )
    ctx = SimpleNamespace(
        slots={}, collection=None, esp_path=None, current_project=None, label_library={}, entry_labels={}
    )
    workbench = SimpleNamespace(filtered_entries=lambda: (), locate_entry=lambda _: None)
    window = AITranslatorWindow(ctx, workbench)
    entry = SimpleNamespace(id="one")
    tasks = [SimpleNamespace(entries=(entry,), translate_entries=(entry,), esp_path="source.esp")]
    try:
        _window_actions.update_window_quick_run(window, config=config, tasks=tasks)
        assert window._view.controls.start_btn.isEnabled()
        assert "本次处理 1 条" in window._view.controls.preflight_label.accessibleDescription()
        config.api_key = ""
        _window_actions.update_window_quick_run(window, config=config, tasks=tasks)
        assert not window._view.controls.start_btn.isEnabled()
        assert "API Key" in window._view.controls.preflight_label.accessibleDescription()
        assert "Embedding" not in window._view.controls.preflight_label.accessibleDescription()
    finally:
        window.close()
        _APP.processEvents()
