"""Display and execution share local configuration readiness without exposing keys."""

from dataclasses import asdict
from types import SimpleNamespace

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.service_readiness import read_service_availability, service_availability


@pytest.mark.parametrize("field,label", [("api_key", "API Key"), ("model", "模型名称"), ("base_url", "服务地址")])
def test_missing_configuration_is_specific_and_secret_free(field, label):
    config = LLMConfig(model="example", api_key="secret-not-for-ui")
    setattr(config, field, "")
    status = service_availability(config)
    assert not status.ready and field in status.missing_fields
    assert label in status.message
    assert "secret-not-for-ui" not in repr(asdict(status))


def test_configuration_change_is_read_again_without_relying_on_model_name(monkeypatch):
    config = LLMConfig(model="example")
    monkeypatch.setattr(LLMConfig, "load_from_file", classmethod(lambda cls: config))
    assert not read_service_availability().ready
    config.api_key = "new-secret"
    assert read_service_availability().ready
    config.base_url = "not-an-endpoint"
    assert not read_service_availability().ready
    assert "HTTP(S)" in read_service_availability().message
    config.base_url = "http://localhost:1234/v1"
    assert read_service_availability().ready


def test_configuration_failure_has_recoverable_public_message(monkeypatch):
    def fail(cls):
        raise OSError("storage unavailable")

    monkeypatch.setattr(LLMConfig, "load_from_file", classmethod(fail))
    status = read_service_availability()
    assert not status.ready and status.missing_fields == ("configuration",)
    assert "设置" in status.message


def test_execution_resolves_changed_credentials_with_unchanged_ini_timestamp(monkeypatch):
    from transbridge.smart_assistant.conversation_orchestrator import ConversationOrchestrator

    config = LLMConfig(model="example")
    monkeypatch.setattr(LLMConfig, "load_from_file", classmethod(lambda cls: config.copy_for_execution()))
    monkeypatch.setattr("transbridge.smart_assistant.conversation_orchestrator.os.path.getmtime", lambda _: 5)
    created = []

    def create(config):
        client = object()
        created.append(client)
        return client

    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", create)
    orchestrator = SimpleNamespace(_llm_client=None, _llm_client_config_hash=None, _llm_config_mtime=None)
    assert ConversationOrchestrator._get_llm_client(orchestrator) is None
    config.api_key = "secret-added-to-credential-store"
    first = ConversationOrchestrator._get_llm_client(orchestrator)
    assert first is not None and len(created) == 1
    assert ConversationOrchestrator._get_llm_client(orchestrator) is first
    config.api_key = ""
    assert ConversationOrchestrator._get_llm_client(orchestrator) is None
