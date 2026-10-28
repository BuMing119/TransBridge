"""Translation configuration queries use the canonical settings implementation."""

import json
from types import SimpleNamespace

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.tools.tool_translator import TranslationController
from transbridge.smart_assistant.tools.translation_settings import TranslationSettings


@pytest.mark.parametrize("controller", [TranslationSettings, TranslationController])
def test_configuration_query_reports_postprocess_terms_and_remote_status(tmp_path, monkeypatch, controller):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "Example_terms.json").write_text(json.dumps({"term": "value"}), encoding="utf-8")
    config = LLMConfig(api_key="test", pp_enable_polish=True, pp_enable_consistency_check=False)
    monkeypatch.setattr("transbridge.smart_assistant.tools._common.load_llm_config", lambda: config)
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.load_from_file",
        lambda: SimpleNamespace(token="configured", base_url="https://example.test"),
    )

    result = controller().get_translation_config({}, SimpleNamespace(esp_path="Example.esp"))

    assert result.success
    assert result.data["post_process"]["polish"] is True
    assert result.data["post_process"]["consistency_check"] is False
    assert result.data["term_database"] == {
        "path": str(data.relative_to(tmp_path) / "Example_terms.json"),
        "entry_count": 1,
    }
    assert result.data["paratranz"] == {"token_configured": True, "api_url": "https://example.test"}
    assert "test" not in result.data.values()


def test_configuration_query_reports_unreadable_terms_without_losing_configuration(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "Example_terms.json").write_text("invalid-json", encoding="utf-8")
    monkeypatch.setattr("transbridge.smart_assistant.tools._common.load_llm_config", LLMConfig)
    monkeypatch.setattr(
        "transbridge.paratranz.config_manager.ParatranzConfig.load_from_file",
        lambda: SimpleNamespace(token="", base_url="https://example.test"),
    )

    result = TranslationSettings().get_translation_config({}, SimpleNamespace(esp_path="Example.esp"))

    assert result.success
    assert result.data["term_database"] == {"path": None, "entry_count": 0}
    assert "术语数据库信息读取失败" in caplog.text
