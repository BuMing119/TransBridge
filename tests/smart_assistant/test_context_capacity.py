from dataclasses import replace

import pytest

from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.context_budget import ContextBudget, ContextBudgetExceeded, budget_for_config
from transbridge.smart_assistant.context_capacity import resolve_context_capacity
from transbridge.smart_assistant.context_estimation import estimate_tokens, select_estimator


def official(**kwargs):
    return replace(LLMConfig(model="deepseek-v4-flash", base_url="https://api.deepseek.com/v1"), **kwargs)


def test_auto_capacity_follows_verified_model_and_preserves_explicit_override():
    config = official()
    assert resolve_context_capacity(config).window == 1_000_000
    assert resolve_context_capacity(replace(config, model="deepseek-v4-pro")).window == 1_000_000
    assert resolve_context_capacity(replace(config, assistant_context_window=32768)).window == 32768
    assert resolve_context_capacity(config, override=16384).window == 16384


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "deepseek-v4-flash-custom"},
        {"base_url": "https://proxy.example/v1"},
        {"base_url": "https://api.deepseek.com.evil.example/v1"},
        {"base_url": "https://api.deepseek.com/custom"},
        {"base_url": "http://api.deepseek.com"},
        {"provider": "anthropic"},
    ],
)
def test_unknown_model_or_endpoint_has_explicit_fallback(changes):
    capacity = resolve_context_capacity(official(**changes))
    assert capacity.window == 131072
    assert "默认 128K" in capacity.source


def test_default_budget_and_unknown_service_accept_fifty_thousand_tokens():
    messages = [{"role": "user", "content": "word " * 25000}]
    default = ContextBudget()
    unknown = budget_for_config(LLMConfig(model="unknown-model"))
    assert default.context_window == unknown.context_window == 131072
    assert 50000 < default.require(messages).total < 131072
    assert unknown.require(messages).fits


def test_offline_estimate_handles_mixed_text_without_counting_bytes_as_tokens():
    assert estimate_tokens("") == 0
    assert 0 < estimate_tokens("hello world " * 100) < 1200
    assert 0 < estimate_tokens("这是中文翻译。" * 100) < len(("这是中文翻译。" * 100).encode())
    assert estimate_tokens("🙂" * 100) > 0


def test_estimator_never_loads_tokenizer_assets(monkeypatch):
    import tiktoken
    import tiktoken.registry

    def forbidden(*args, **kwargs):
        pytest.fail("must not load or download an encoding")

    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})
    counter, label = select_estimator("gpt-4o")
    assert counter("<|endoftext|> 中文") > 0
    assert label == "text-v2-estimated-25pct"


def test_ready_tokenizer_counts_special_looking_user_text_as_plain_text(monkeypatch):
    import tiktoken.registry

    class Encoding:
        def encode(self, text, *, disallowed_special):
            assert disallowed_special == ()
            return [1, 2, 3, 4]

    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {"o200k_base": Encoding()})
    counter, label = select_estimator("gpt-4o")
    assert counter("<|endoftext|>") == 5
    assert label == "o200k_base-estimated-25pct"


def test_fifty_thousand_estimated_tokens_fit_official_capacity_but_honor_manual_limit():
    messages = [{"role": "user", "content": "word " * 25000}]
    automatic = budget_for_config(official())
    assert automatic.require(messages).total > 50000
    manual = budget_for_config(official(assistant_context_window=32768))
    with pytest.raises(ContextBudgetExceeded, match="配置窗口 32,768") as error:
        manual.require(messages)
    assert "消息" in str(error.value) and "工具定义" in str(error.value)
    assert "不是服务端实际用量" in str(error.value)


def test_all_builtin_tool_definitions_fit_official_auto_capacity():
    from transbridge.smart_assistant.native_tools import build_native_tool_definitions
    from transbridge.smart_assistant.tool_registry import ToolRegistry
    from transbridge.smart_assistant.tools import register_all

    register_all()
    tools = build_native_tool_definitions(tuple(ToolRegistry.list_all_namespaces()), request_stage="execution")
    assert len(tools) > 50
    usage = budget_for_config(official()).require([{"role": "user", "content": "测试工具"}], tools)
    assert usage.tool_schemas > 0
