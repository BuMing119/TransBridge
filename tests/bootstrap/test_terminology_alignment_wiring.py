from dataclasses import replace

import pytest

from transbridge.bootstrap.terminology_alignment import prepare_alignment
from transbridge.config.llm import LLMConfig


def _config():
    return LLMConfig(
        provider="openai_compatible",
        base_url="https://example.invalid/v1",
        model="example-model",
        api_key="test-key",
        max_concurrent=3,
    )


def test_preparation_freezes_settings_and_open_closes_owned_client(monkeypatch):
    config = _config()
    received = []
    closed = []

    class Client:
        def close(self):
            closed.append(True)

    def factory(value):
        received.append(value)
        return Client()

    monkeypatch.setattr("transbridge.bootstrap.terminology_alignment.with_reasoning_intent", lambda raw, *_args: raw)
    prepared = prepare_alignment(config_loader=lambda: config, client_factory=factory)
    config.model = "changed"
    assert received == []
    assert prepared.display_label == "openai_compatible / example-model"
    assert "test-key" not in repr(prepared)
    with prepared.open() as service:
        assert received[0].model == "example-model"
        assert received[0] is not config
        assert service.resolve_many(()) == ()
        assert closed == []
    assert closed == [True]


@pytest.mark.parametrize(
    "changes",
    [
        {"api_key": ""},
        {"model": " "},
        {"base_url": ""},
        {"max_concurrent": 0},
        {"max_concurrent": True},
        {"max_tokens_per_batch": 0},
        {"max_output_tokens": -1},
        {"llm_max_retries": -1},
    ],
)
def test_invalid_settings_are_rejected_before_client_creation(changes):
    def factory(_config):
        raise AssertionError("must not initialize transport")

    with pytest.raises(ValueError):
        prepare_alignment(config_loader=lambda: replace(_config(), **changes), client_factory=factory)


def test_client_is_closed_when_operation_raises(monkeypatch):
    closed = []

    class Client:
        def close(self):
            closed.append(True)

    monkeypatch.setattr("transbridge.bootstrap.terminology_alignment.with_reasoning_intent", lambda raw, *_args: raw)
    prepared = prepare_alignment(config_loader=_config, client_factory=lambda _config: Client())
    with pytest.raises(RuntimeError, match="request processing"):
        with prepared.open():
            raise RuntimeError("request processing")
    assert closed == [True]
