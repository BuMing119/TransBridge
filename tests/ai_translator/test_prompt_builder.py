"""PromptBuilder 翻译提示词分层与内部缓存标记测试（Story 15）。

覆盖：三消息结构、模式归一化、动态 USER 内容、术语顺序/空值行为、
通用 SYSTEM 稳定缓存 key 与 A/B 标记。镜像结构位于 tests/ai_translator/。

说明：为避免 DSH 沙箱对 pytest tmp 清理的限制，本测试通过 monkeypatch
模块级 _load_toml 注入受控模板，不依赖真实文件系统。
"""

from __future__ import annotations

import json
import re

import pytest

from transbridge.ai_translator import prompt_builder as prompt_builder_mod
from transbridge.ai_translator.prompt_builder import PromptBuilder
from transbridge.ai_translator.structured_schemas import TERM_EXTRACTION_OUTPUT_SCHEMA, TRANSLATION_OUTPUT_SCHEMA
from transbridge.config.language_profiles import LanguageProfile, LanguageProfileError
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra.llm_structured_outputs import extract_structured_output_directive
from transbridge.infra.prompt_cache import (
    PROMPT_CACHE_METADATA_KEY,
    extract_prompt_cache_directives,
)

# ── 受控模板 ─────────────────────────────────────────────────────────────────

_COMMON_SYSTEM = """\
<game_and_language_pair>
Game: $game_name
Source language: $source_lang
Target language: $target_lang
</game_and_language_pair>

<role_and_objective>
You are a professional $game_name mod localization translator.
</role_and_objective>

<general_translation_rules>
1. Produce natural, fluent translations.
2. $format_notes
</general_translation_rules>

$fixed_examples
"""

_MODE_SYSTEM = "<translation_mode>$translation_mode</translation_mode>"

_USER = """\
<task_category>$batch_type</task_category>

<translation_entries>
$input_json
</translation_entries>

Return one translation for every input entry and preserve each entry ID exactly.
"""

_GAME_TOML = {
    "game": {
        "name": "The Elder Scrolls V: Skyrim Special Edition (SSE)",
        "format_notes": "Preserve special markers.",
    }
}
_LANGUAGE = LanguageProfile(
    locale="zh_CN",
    display_name="Simplified Chinese",
    target_language="Simplified Chinese",
    source_language="English",
    example_source="Hello.",
    example_target="你好。",
)


def _fake_load_toml(translation: dict | None = None):
    """构造一个受控的 _load_toml，按路径返回游戏或阶段模板。"""

    def loader(path):
        p = str(path)
        if "games" in p:
            return _GAME_TOML
        if "translation" in p:
            return {
                "translation": {
                    "common_system": (translation or {}).get("common", _COMMON_SYSTEM),
                    "mode_system": (translation or {}).get("mode", _MODE_SYSTEM),
                    "user": (translation or {}).get("user", _USER),
                }
            }
        if "extraction" in p:
            return {"extraction": {}}
        return {}

    return loader


def _builder(monkeypatch, translation=None) -> PromptBuilder:
    monkeypatch.setattr(prompt_builder_mod, "_load_toml", _fake_load_toml(translation))
    monkeypatch.setattr(prompt_builder_mod, "load_language_profile", lambda *_args, **_kwargs: _LANGUAGE)
    return PromptBuilder("skyrim_se", "zh_CN")


@pytest.fixture
def builder(monkeypatch):
    return _builder(monkeypatch)


def _entry(key: str, original: str) -> TranslationEntry:
    return TranslationEntry(
        id=key,
        key=key,
        original=original,
        translation="",
        stage=0,
        context="NPC_:FULL",
    )


def _translation_payload(user_content: str) -> dict:
    start = user_content.index("<translation_entries>") + len("<translation_entries>")
    end = user_content.index("</translation_entries>")
    return json.loads(user_content[start:end].strip())


# ── 结构测试 ─────────────────────────────────────────────────────────────────


def test_returns_strict_three_messages(builder):
    """返回消息严格为 system(A) -> system(B) -> user。"""
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")
    assert [m["role"] for m in msgs] == ["system", "system", "user"]

    directives = extract_prompt_cache_directives(msgs)[1]
    assert [d["breakpoint"] for d in directives] == ["A", "B"]
    assert all(d["profile"] == "translation_layered" for d in directives)
    # A/B 共用同一 key
    assert directives[0]["key"] == directives[1]["key"]


def test_messages_carry_internal_cache_metadata(builder):
    """A/B 内部标记挂在对应的 system 消息上，用户消息不挂。"""
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")
    assert msgs[0][PROMPT_CACHE_METADATA_KEY]["breakpoint"] == "A"
    assert msgs[1][PROMPT_CACHE_METADATA_KEY]["breakpoint"] == "B"
    assert PROMPT_CACHE_METADATA_KEY not in msgs[2]
    assert msgs[2]["role"] == "user"


def test_translation_messages_carry_native_output_schema(builder):
    messages = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")

    clean_messages, output_schema = extract_structured_output_directive(messages)

    assert output_schema == TRANSLATION_OUTPUT_SCHEMA
    assert all("_transbridge_structured_output" not in message for message in clean_messages)


def test_extraction_messages_carry_native_output_schema(builder):
    messages = builder.build_extraction_prompt([{"original": "Riverwood", "translation": "溪木镇"}])

    _clean_messages, output_schema = extract_structured_output_directive(messages)

    assert output_schema == TERM_EXTRACTION_OUTPUT_SCHEMA


def test_common_system_contains_stable_sections(builder):
    """通用 SYSTEM 含游戏语言对、角色、通用规范和固定语义示例。"""
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")
    content = msgs[0]["content"]
    for marker in (
        "game_and_language_pair",
        "role_and_objective",
        "general_translation_rules",
        "fixed_examples",
        "The Elder Scrolls V: Skyrim Special Edition (SSE)",
        "English",
        "Simplified Chinese",
    ):
        assert marker in content


def test_prompt_text_does_not_duplicate_native_output_schema(builder):
    translation_messages = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")
    extraction_messages = builder.build_extraction_prompt([{"original": "Riverwood", "translation": "溪木镇"}])

    rendered = "\n".join(message["content"] for message in (*translation_messages, *extraction_messages))

    for fragment in ('"results":', '"entry_id":', '"term":', "strict JSON object", "JSON output protocol"):
        assert fragment not in rendered
    assert "Source: Hello." in rendered
    assert "Translation: 你好。" in rendered


def test_common_system_excludes_dynamic_content(builder):
    """通用 SYSTEM 不含具体分类、动态术语和输入文本。"""
    msgs = builder.build_translation_prompt([_entry("k1", "Greetings, traveler!")], {"Dragon": "龙"}, "人名")
    content = msgs[0]["content"]
    assert "人名" not in content
    assert "Dragon" not in content
    assert "Greetings, traveler!" not in content
    assert "k1" not in content
    assert "mandatory_terminology" not in content


def test_mode_system_only_declares_mode_label(builder):
    """模式 SYSTEM 只含三种模式之一，不含专属规则。"""
    mode_msg = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")[1]["content"]
    assert mode_msg.strip() == "<translation_mode>entity_short_text</translation_mode>"


# ── 模式归一化测试 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "batch_type",
    ["种族与派系", "人名", "地名", "书名", "物品", "法术技能", "任务名", "互动"],
)
def test_entity_batch_types_map_to_entity_short(builder, batch_type):
    assert builder._translation_mode(batch_type) == "entity_short_text"


def test_dialogue_maps_to_dialogue(builder):
    assert builder._translation_mode("对话") == "dialogue"


@pytest.mark.parametrize("batch_type", ["长文本", "未分类", "未来未知分类", ""])
def test_unknown_maps_to_long_text(builder, batch_type):
    assert builder._translation_mode(batch_type) == "long_text"


def test_user_renders_english_batch_type_label(builder):
    """The dynamic User prompt localizes the category without changing shared business labels."""
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "地名")
    assert "<task_category>locations</task_category>" in msgs[2]["content"]


def test_user_preserves_unknown_dynamic_batch_type(builder):
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "未来未知分类")
    assert "<task_category>未来未知分类</task_category>" in msgs[2]["content"]


# ── 动态 USER 内容测试 ───────────────────────────────────────────────────────


def test_user_json_scopes_terms_to_their_entry_and_preserves_order(builder):
    """每条只携带自己的术语，并保持术语迭代顺序。"""
    entries = [_entry("k1", "A and B"), _entry("k2", "C")]
    user_content = builder.build_translation_prompt(
        entries,
        {"A": "甲", "B": "乙", "C": "丙"},
        "人名",
        terms_by_entry={"k1": {"A": "甲", "B": "乙"}, "k2": {"C": "丙"}},
    )[2]["content"]
    payload = _translation_payload(user_content)
    assert list(payload) == ["k1", "k2"]
    assert list(payload["k1"]["terms"]) == ["A", "B"]
    assert payload["k2"]["terms"] == {"C": "丙"}
    assert "mandatory_terminology" not in user_content


def test_user_terms_field_omitted_when_empty(builder):
    """无术语时省略条目的 terms 字段。"""
    user_content = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")[2]["content"]
    assert _translation_payload(user_content) == {"k1": {"source": "Hello"}}
    assert "terms" not in _translation_payload(user_content)["k1"]


def test_user_json_uses_nested_input_but_keeps_entry_order(builder):
    """输入改为 source/terms 嵌套项，同时保持条目顺序。"""
    entries = [_entry("k2", "你好"), _entry("k1", "World")]
    user_content = builder.build_translation_prompt(entries, {}, "人名")[2]["content"]

    expected = json.dumps(
        {"k2": {"source": "你好"}, "k1": {"source": "World"}},
        ensure_ascii=False,
        indent=2,
    )
    assert f"<translation_entries>\n{expected}\n</translation_entries>" in user_content


def test_flat_terms_fallback_only_binds_terms_visible_in_same_source(builder):
    """旧调用未传逐条映射时，不把不可定位的平面术语复制给所有条目。"""
    entries = [_entry("dragon", "Dragon attacks"), _entry("other", "Hello")]
    user_content = builder.build_translation_prompt(
        entries,
        {"Dragon": "龙", "SemanticOnly": "语义术语"},
        "互动",
    )[2]["content"]

    assert _translation_payload(user_content) == {
        "dragon": {"source": "Dragon attacks", "terms": {"Dragon": "龙"}},
        "other": {"source": "Hello"},
    }


def test_shared_term_is_serialized_in_each_related_entry(builder):
    entries = [_entry("a", "Dragon one"), _entry("b", "Dragon two")]
    user_content = builder.build_translation_prompt(
        entries,
        {"Dragon": "龙"},
        "互动",
        terms_by_entry={"a": {"Dragon": "龙"}, "b": {"Dragon": "龙"}},
    )[2]["content"]

    payload = _translation_payload(user_content)
    assert payload["a"]["terms"] == {"Dragon": "龙"}
    assert payload["b"]["terms"] == {"Dragon": "龙"}


# ── 缓存 key 测试 ────────────────────────────────────────────────────────────


def test_same_common_system_same_key(builder):
    """相同通用 SYSTEM 产生相同 key。"""
    k1 = builder.build_translation_prompt([_entry("a", "Hello")], {}, "人名")[0][PROMPT_CACHE_METADATA_KEY]["key"]
    k2 = builder.build_translation_prompt([_entry("b", "World")], {"X": "Y"}, "对话")[0][PROMPT_CACHE_METADATA_KEY][
        "key"
    ]
    assert k1 == k2


def test_key_stable_under_dynamic_variation(builder):
    """具体分类、模式、术语、输入文本不改变 common cache key。"""
    keys = {
        builder.build_translation_prompt([_entry("a", "Hello")], {}, "人名")[0][PROMPT_CACHE_METADATA_KEY]["key"],
        builder.build_translation_prompt([_entry("a", "Hello")], {"D": "龙"}, "对话")[0][PROMPT_CACHE_METADATA_KEY][
            "key"
        ],
        builder.build_translation_prompt([_entry("b", "Other")], {}, "长文本")[0][PROMPT_CACHE_METADATA_KEY]["key"],
    }
    assert len(keys) == 1


def test_key_changes_when_common_template_changes(monkeypatch):
    """改变通用模板后 key 改变。"""
    b1 = _builder(monkeypatch, translation={"common": _COMMON_SYSTEM})
    b2 = _builder(monkeypatch, translation={"common": _COMMON_SYSTEM + "\n额外规则：……"})
    assert b1._translation_cache_key != b2._translation_cache_key


def test_cache_key_format(builder):
    """key 格式为 transbridge.translation.v2.<sha256[:24]>。"""
    key = builder._translation_cache_key
    assert key.startswith("transbridge.translation.v2.")
    assert len(key.split(".")[-1]) == 24
    assert key.count(".") == 3


def test_b_and_a_share_same_key(builder):
    """A、B 使用同一 key，B 靠 breakpoint 区分。"""
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "对话")
    a_key = msgs[0][PROMPT_CACHE_METADATA_KEY]["key"]
    b_key = msgs[1][PROMPT_CACHE_METADATA_KEY]["key"]
    assert a_key == b_key


# ── 严格语言档案与模板 fallback ──────────────────────────────────────────────


def test_fallback_defaults_when_prompt_toml_missing(monkeypatch):
    """阶段模板缺失时使用语言中立的内置模板。"""
    monkeypatch.setattr(prompt_builder_mod, "_load_toml", lambda path: {})
    monkeypatch.setattr(prompt_builder_mod, "load_language_profile", lambda *_args, **_kwargs: _LANGUAGE)
    b = PromptBuilder("skyrim_se", "zh_CN")

    msgs = b.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")
    assert [m["role"] for m in msgs] == ["system", "system", "user"]
    directives = extract_prompt_cache_directives(msgs)[1]
    assert [d["breakpoint"] for d in directives] == ["A", "B"]
    assert "Simplified Chinese" in msgs[0]["content"]
    assert '"results":' not in msgs[0]["content"]
    assert '"entry_id":' not in msgs[0]["content"]


def test_missing_language_profile_fails_instead_of_falling_back(monkeypatch):
    monkeypatch.setattr(prompt_builder_mod, "_load_toml", lambda path: {})

    def missing(*_args, **_kwargs):
        raise LanguageProfileError("Unsupported language profile 'ja_JP'")

    monkeypatch.setattr(prompt_builder_mod, "load_language_profile", missing)
    with pytest.raises(LanguageProfileError, match="ja_JP"):
        PromptBuilder("skyrim_se", "ja_JP")


def test_static_prompt_scaffold_is_english(builder):
    """Chinese is allowed in target examples and runtime data, but not in instruction scaffolding."""
    messages = builder.build_translation_prompt(
        [_entry("k1", "Dragon")],
        {"Dragon": "Dragon target"},
        "人名",
    )
    common = re.sub(r"<fixed_examples>[\s\S]*?</fixed_examples>", "", messages[0]["content"])
    assert re.search(r"[\u4e00-\u9fff]", common) is None
    assert re.search(r"[\u4e00-\u9fff]", messages[1]["content"]) is None
    assert re.search(r"[\u4e00-\u9fff]", messages[2]["content"]) is None


def test_terms_never_reattached_to_system(builder):
    """动态术语只进入对应 USER JSON 项，绝不进入 SYSTEM。"""
    terms = {"Dragon": "龙", "Whiterun": "白漫城"}
    msgs = builder.build_translation_prompt(
        [_entry("k1", "Dragon")],
        terms,
        "人名",
        terms_by_entry={"k1": {"Dragon": "龙"}},
    )
    assert "Dragon" not in msgs[0]["content"]
    assert "Whiterun" not in msgs[0]["content"]
    assert _translation_payload(msgs[2]["content"])["k1"]["terms"] == {"Dragon": "龙"}
    assert "Whiterun" not in msgs[2]["content"]


def test_parse_translation_response_reads_results_envelope_and_rejects_duplicates(builder):
    response = json.dumps({
        "results": [
            {"entry_id": "a", "translation": "甲"},
            {"entry_id": "b", "translation": "乙"},
            {"entry_id": "b", "translation": "重复"},
            {"entry_id": "unknown", "translation": "忽略"},
        ]
    })

    assert builder.parse_translation_response(response, {"a", "b"}) == {"a": "甲"}


def test_extract_partial_pairs_returns_only_complete_results_items(builder):
    complete_prefix = '{"results":[{"entry_id":"a","translation":"甲"},'
    incomplete_item = '{"entry_id":"b","translation":"未完成'

    assert builder.extract_partial_pairs(complete_prefix + incomplete_item) == {"a": "甲"}


def test_parse_extraction_response_reads_results_envelope(builder):
    response = json.dumps({"results": [{"term": "Riverwood", "translation": "溪木镇"}]})

    assert builder.parse_extraction_response(response) == [{"term": "Riverwood", "translation": "溪木镇"}]
