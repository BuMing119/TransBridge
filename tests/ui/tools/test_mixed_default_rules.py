import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository
from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.tools.ai_translator._rule_editor_widget import _RuleEditorWidget
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow


@pytest.fixture
def task_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "config.ini"
    repository = ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    repository.update_sections({
        "llm": {
            "provider": "openai_compatible",
            "base_url": "https://example.test/v1",
            "model": "test-model",
            "enable_semantic_match": False,
        }
    })
    load_config = LLMConfig.load_from_file
    monkeypatch.setattr(
        LLMConfig,
        "load_from_file",
        lambda: load_config(repository=repository, environment={"TRANSBRIDGE_LLM_API_KEY": "test-key"}),
    )
    profiles = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    monkeypatch.setattr("transbridge.config.ai_workflow_profiles.AiWorkflowProfileRepository", lambda: profiles)
    windows = []

    def open_window(*, has_translation=True):
        entries = [TranslationEntry("new", "new", "Source", "", 0, "NPC_:FULL")]
        if has_translation:
            entries.append(TranslationEntry("done", "done", "Other source", "已有译文", 1, "NPC_:FULL"))
        collection = TranslationEntryCollection(entries)
        slot = SimpleNamespace(label="Demo", collection=collection, esp_path="Demo.esp")
        context = SimpleNamespace(
            slots={"demo": slot},
            active_slot=slot,
            collection=collection,
            esp_path=slot.esp_path,
            current_project=None,
            label_library={},
            entry_labels={},
        )
        workbench = SimpleNamespace(filtered_entries=lambda: tuple(collection), locate_entry=lambda _: None)
        window = AITranslatorWindow(context, workbench)
        windows.append(window)
        return window

    yield open_window, repository
    for window in windows:
        window.close()
    app.processEvents()


@pytest.mark.parametrize("raw", [None, ""])
@pytest.mark.parametrize("has_translation", [False, True])
def test_mixed_defaults_work_on_open_switch_and_reopen(task_window, raw, has_translation):
    open_window, repository = task_window
    repository.update_sections({"llm": {"action_rules": raw}})
    before = repository.path.read_bytes()

    for _ in range(2):
        window = open_window(has_translation=has_translation)
        controls = window._view.controls
        for mode in ("mixed", "translate", "polish", "mixed"):
            getattr(controls, f"mode_{mode}").click()
            QApplication.processEvents()
            if mode == "mixed":
                (task,) = window._task_sources()
                assert [entry.key for entry in task.translate_entries] == ["new"]
                assert [entry.key for entry in task.polish_entries] == (["done"] if has_translation else [])
                assert controls.start_btn.isEnabled(), controls.preflight_label.full_text
                assert f"本次任务 {2 if has_translation else 1} 条" in controls.estimate_lbl.text()
        window.close()

    assert repository.path.read_bytes() == before


@pytest.mark.parametrize("rules", [[], [{"rule_id": "custom", "priority": 0, "action": "skip"}]])
def test_saved_empty_or_custom_rules_are_not_replaced_on_switch(task_window, rules):
    open_window, repository = task_window
    repository.update_sections({"llm": {"action_rules": json.dumps(rules)}})
    before = repository.path.read_bytes()
    window = open_window()
    controls = window._view.controls

    for mode in ("mixed", "translate", "mixed"):
        getattr(controls, f"mode_{mode}").click()
        QApplication.processEvents()
        actual = controls.rule_editor.get_rules()
        assert [(rule.rule_id, rule.action) for rule in actual] == [(rule["rule_id"], rule["action"]) for rule in rules]
        if mode == "mixed":
            assert not window._task_sources()[0].entries
            assert not controls.start_btn.isEnabled()

    window.close()
    assert repository.path.read_bytes() == before


def test_rule_editor_reset_uses_the_same_defaults_as_configuration(task_window):
    editor = _RuleEditorWidget()
    try:
        editor.set_rules([])
        editor.reset_to_default()
        assert editor.get_rules() == LLMConfig().action_rules
    finally:
        editor.close()
