"""共享后处理提示词模板契约测试。"""

import pytest

from transbridge.ai_translator.post_processor.prompt_contract import (
    PromptTemplateContractError,
    render_prompt_template,
    validate_prompt_template,
)
from transbridge.ai_translator.post_processor.quality_gate import _resolve_template


def test_invalid_template_syntax_is_rejected_during_validation():
    with pytest.raises(PromptTemplateContractError, match="模板语法无效"):
        validate_prompt_template(
            name="broken.system",
            template="$",
            allowed_variables=frozenset({"game_name"}),
            required_variables=frozenset(),
        )


def test_invalid_template_syntax_is_rejected_during_render():
    with pytest.raises(PromptTemplateContractError, match="模板语法无效"):
        render_prompt_template(
            name="broken.system",
            template="$",
            values={"game_name": "Skyrim"},
        )


def test_missing_template_variable_is_still_rejected_during_render():
    with pytest.raises(PromptTemplateContractError, match="缺少变量 'original'"):
        render_prompt_template(name="single.user", template="Source: $original", values={})


@pytest.mark.parametrize("text", ["Welcome, ${player_name}!", "$SKSE_VERSION", "Press `E` near <Alias=Hero>."])
def test_dynamic_text_is_not_reinterpreted_as_template_syntax(text):
    rendered = render_prompt_template(
        name="single.user",
        template="Source: $original\nLiteral dollar: $$game_name",
        values={"original": text},
    )

    assert rendered == f"Source: {text}\nLiteral dollar: $game_name"


def test_invalid_quality_gate_variant_falls_back_to_default():
    with pytest.warns(UserWarning, match="回退到内置默认模板"):
        resolved = _resolve_template(
            name="quality_gate.single.system",
            template="$",
            default="valid default",
            allowed_variables=frozenset({"game_name"}),
            required_variables=frozenset(),
        )

    assert resolved == "valid default"
