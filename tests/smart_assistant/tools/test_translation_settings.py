"""Translation configuration queries use the canonical settings implementation."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.tools.tool_translator import TranslationController
from transbridge.smart_assistant.tools.translation_settings import TranslationSettings


@pytest.mark.parametrize("esp_path", ["Example.esp", None])
def test_configuration_query_reads_editor_database_and_current_remote_status(tmp_path, monkeypatch, esp_path):
    from transbridge.ui.tools.ai_translator import _term_editor_dialog, config_dialogs

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("transbridge.config.llm.get_data_dir", lambda: str(tmp_path))
    data = tmp_path / "data"
    data.mkdir()
    (data / "Example_terms.json").write_text('{"wrong":"location","unused":"file"}', encoding="utf-8")
    opened = []

    class Editor:
        def __init__(self, database, parent):
            self.database = database

        def exec(self):
            self.database.add("Sword", "长剑", "manual")
            self.database.save()
            opened.append(self.database.path)

    monkeypatch.setattr(_term_editor_dialog, "_TermEditorDialog", Editor)
    config_dialogs.open_term_editor(None, esp_path, source_key="source-1")
    config = LLMConfig(api_key="test", pp_enable_polish=True, pp_enable_consistency_check=False)
    monkeypatch.setattr("transbridge.smart_assistant.tools._common.load_llm_config", lambda: config)
    remote = SimpleNamespace(credential_configured=True, base_url="https://example.test")
    ctx = SimpleNamespace(esp_path=esp_path, active_key="source-1", config=remote)
    result = TranslationController().get_translation_config({}, ctx)

    assert result.success
    assert result.data["post_process"]["polish"] is True
    assert result.data["post_process"]["consistency_check"] is False
    assert result.data["term_database"] == {
        "path": opened[0],
        "entry_count": 1,
    }
    assert result.data["paratranz"] == {"token_configured": True, "api_url": "https://example.test"}
    assert "test" not in result.data.values()


def test_configuration_query_reports_unreadable_terms_without_losing_configuration(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("transbridge.config.llm.get_data_dir", lambda: str(tmp_path))
    data = tmp_path / "ai_translator" / "Example"
    data.mkdir(parents=True)
    database = data / "Example_terms.json"
    database.write_text("invalid-json", encoding="utf-8")
    monkeypatch.setattr("transbridge.smart_assistant.tools._common.load_llm_config", LLMConfig)
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.load_from_file",
        lambda: SimpleNamespace(credential_configured=False, base_url="https://example.test"),
    )

    result = TranslationSettings().get_translation_config({}, SimpleNamespace(esp_path="Example.esp"))

    assert result.success
    assert result.data["term_database"] == {"path": str(database), "entry_count": 0}
    assert "加载动态术语库失败" in caplog.text
    missing = TranslationSettings().get_translation_config({}, SimpleNamespace(esp_path="Untouched.esp"))
    assert missing.success and missing.data["term_database"]["entry_count"] == 0
    assert not Path(missing.data["term_database"]["path"]).parent.exists()


def test_settings_center_and_assistant_persist_the_same_service_and_term_edits(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QApplication

    from tests.contracts.config.test_unified_repository import _repository
    from transbridge.ui.settings.ai_defaults_page import AiDefaultsPage
    from transbridge.ui.settings.ai_service_page import AiServicePage
    from transbridge.ui.settings.draft import SettingsConfigDraft
    from transbridge.ui.settings.terminology_page import TerminologySettingsPage

    app = QApplication.instance() or QApplication([])
    assert app is not None
    desktop_repo = _repository(tmp_path / "desktop")
    assistant_repo = _repository(tmp_path / "assistant")
    draft = SettingsConfigDraft(LLMConfig.load_from_file(repository=desktop_repo))
    ai = AiServicePage(draft.llm)
    glossary = TerminologySettingsPage(draft.llm)
    defaults = AiDefaultsPage(draft.llm)
    ai.provider_combo.setCurrentIndex(ai.provider_combo.findData("anthropic"))
    ai.base_url_edit.setText("  https://service.example/v1  ")
    ai.model_edit.setText("  model-x  ")
    ai.temperature_spin.setValue(0.62)
    ai.output_tokens_spin.setValue(7150)
    glossary.json_edit.setText(" local.json ")
    glossary.csv_edit.setText(" local.csv ")
    glossary.excel_edit.setText(" local.xlsx ")
    glossary.priority_list.insertItem(0, glossary.priority_list.takeItem(3))
    ai.apply_to_draft()
    glossary.apply_to_draft()
    defaults.apply_to_draft()
    assert draft.save().saved
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools._common.load_llm_config",
        lambda: LLMConfig.load_from_file(repository=assistant_repo),
    )
    settings = TranslationSettings()
    assert settings.set_translation_config(
        {
            "provider": "anthropic",
            "base_url": "  https://service.example/v1  ",
            "model": "  model-x  ",
            "target_lang": defaults.target_lang_combo.currentData(),
            "max_tokens": 7150,
            "temperature": 0.62,
        },
        None,
    ).success
    assert settings.set_term_config(
        {
            "term_sources": ["csv", "dynamic", "paratranz", "json", "excel"],
            "json_path": " local.json ",
            "csv_path": " local.csv ",
            "excel_path": " local.xlsx ",
        },
        None,
    ).success
    desktop, assistant = desktop_repo.load(), assistant_repo.load()
    fields = (
        "provider",
        "model",
        "base_url",
        "target_lang",
        "temperature",
        "max_output_tokens",
        "term_priority",
        "local_json_path",
        "local_csv_path",
        "local_excel_path",
    )
    assert all(desktop.value("llm", key) == assistant.value("llm", key) for key in fields)

    for args in (
        {"game_profile": "skyrim"},
        {"temperature": 2.01},
        {"max_tokens": -1},
        {"provider": "other", "model": "m", "base_url": "https://example.test"},
        {"provider": "anthropic", "model": "m", "base_url": "ftp://invalid.test"},
    ):
        assert not settings.set_translation_config(args, None).success
    assert assistant == assistant_repo.load()
