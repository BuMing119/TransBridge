from unittest.mock import Mock

import pytest

from transbridge.infra import prompt_cache

_CLAUDE_API = "https://api.anthropic.com"


def _anthropic_system(model, text="stable rules", *, base_url=_CLAUDE_API):
    messages = [
        prompt_cache.attach_prompt_cache_directive(
            {"role": "system", "content": text},
            cache_key="stable",
            profile="single_stable_prefix",
            breakpoint="FINAL",
        ),
        {"role": "user", "content": "Translate this."},
    ]
    system, _ = prompt_cache.build_anthropic_system_blocks(messages, model=model, base_url=base_url)
    return system[0]


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-haiku-5-5-20261112",
    ],
)
def test_anthropic_5_5_cache_estimates_need_640_tokens(model, monkeypatch):
    estimate = Mock(side_effect=[639, 640])
    monkeypatch.setattr(prompt_cache, "estimate_prompt_tokens", estimate)

    assert prompt_cache._anthropic_cache_min_tokens(model) == 512
    assert "cache_control" not in _anthropic_system(model)
    assert _anthropic_system(model)["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize(
    "model",
    [
        "future-model",
        "tenant-claude-opus-5-5",
        "claude-opus-5-50",
        "claude-opus-5-5-experimental",
        "claude-opus-5-5-20269999",
    ],
)
def test_unknown_anthropic_minimum_is_left_to_the_official_api(model, monkeypatch, caplog):
    caplog.set_level("DEBUG", logger=prompt_cache.__name__)
    estimate = Mock()
    monkeypatch.setattr(prompt_cache, "estimate_prompt_tokens", estimate)

    assert prompt_cache._anthropic_cache_min_tokens(model) is None
    assert _anthropic_system(model)["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in _anthropic_system(model, base_url="https://api.anthropic.com.example")
    assert "strategy=explicit_breakpoints reason=server_checks_unknown_model_minimum" in caplog.text
    assert "strategy=standard_request reason=endpoint_not_official" in caplog.text
    estimate.assert_not_called()


def test_anthropic_unknown_model_does_not_mark_an_empty_block():
    assert "cache_control" not in _anthropic_system("future-model", "")
