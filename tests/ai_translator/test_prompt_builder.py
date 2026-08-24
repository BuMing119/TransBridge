"""PromptBuilder 翻译提示词分层与内部缓存标记测试（Story 15）。

覆盖：三消息结构、模式归一化、动态 USER 内容、术语顺序/空值行为、
通用 SYSTEM 稳定缓存 key 与 A/B 标记。镜像结构位于 tests/ai_translator/。

说明：为避免 DSH 沙箱对 pytest tmp 清理的限制，本测试通过 monkeypatch
模块级 _load_toml 注入受控模板，不依赖真实文件系统。
"""

from __future__ import annotations

import json

import pytest

from transbridge.ai_translator import prompt_builder as prompt_builder_mod
from transbridge.ai_translator.prompt_builder import PromptBuilder
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra.prompt_cache import (
    PROMPT_CACHE_METADATA_KEY,
    extract_prompt_cache_directives,
)

# ── 受控模板 ─────────────────────────────────────────────────────────────────

_COMMON_SYSTEM = """\
<game_and_language_pair>
游戏：$game_name
源语言：$source_lang
目标语言：$target_lang
</game_and_language_pair>

<role_and_objective>
你是专业的 $game_name 模组本地化翻译员。
</role_and_objective>

<general_translation_rules>
1. 措辞自然流畅。
2. $format_notes
</general_translation_rules>

<json_output_protocol>
输出必须是严格的 JSON 对象，格式：{"id1": "译文1", "id2": "译文2", ...}
</json_output_protocol>

<fixed_examples>
输入：{"001": "Hello."}
输出：{"001": "你好。"}
</fixed_examples>
"""

_MODE_SYSTEM = "<translation_mode>$translation_mode</translation_mode>"

_USER = """\
<task_category>$batch_type</task_category>

<translation_entries>
$input_json
</translation_entries>

请严格按 SYSTEM 中的扁平 JSON 输出协议返回结果。
"""

_GAME_TOML = {
    "game": {
        "name": "上古卷轴5：天际特别版（SSE）",
        "format_notes": "保留特殊标记",
    }
}
_LANG_TOML = {
    "lang": {
        "name": "中文（简体）",
        "target": "中文",
        "source": "英文",
    }
}


def _fake_load_toml(translation: dict | None = None, *, legacy: bool = False):
    """构造一个受控的 _load_toml，按路径返回游戏或语言数据。"""

    def loader(path):
        p = str(path)
        if "games" in p:
            return _GAME_TOML
        if "langs" in p:
            lang = dict(_LANG_TOML)
            if legacy:
                lang["translation"] = {
                    "system": "你是专业的 $game_name 翻译员。",
                    "user": "请翻译 $batch_type：\n$input_json",
                }
            else:
                lang["translation"] = {
                    "common_system": (translation or {}).get("common", _COMMON_SYSTEM),
                    "mode_system": (translation or {}).get("mode", _MODE_SYSTEM),
                    "user": (translation or {}).get("user", _USER),
                }
            return lang
        return {}

    return loader


def _builder(monkeypatch, translation=None, *, legacy: bool = False) -> PromptBuilder:
    monkeypatch.setattr(prompt_builder_mod, "_load_toml", _fake_load_toml(translation, legacy=legacy))
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


def test_common_system_contains_stable_sections(builder):
    """通用 SYSTEM 含游戏语言对、角色、通用规范、JSON 协议、固定示例。"""
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")
    content = msgs[0]["content"]
    for marker in (
        "game_and_language_pair",
        "role_and_objective",
        "general_translation_rules",
        "json_output_protocol",
        "fixed_examples",
        "上古卷轴5：天际特别版（SSE）",
        "英文",
        "中文",
    ):
        assert marker in content


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
    assert mode_msg.strip() == "<translation_mode>实体短文本</translation_mode>"


# ── 模式归一化测试 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "batch_type",
    ["种族与派系", "人名", "地名", "书名", "物品", "法术技能", "任务名", "互动"],
)
def test_entity_batch_types_map_to_entity_short(builder, batch_type):
    assert builder._translation_mode(batch_type) == "实体短文本"


def test_dialogue_maps_to_dialogue(builder):
    assert builder._translation_mode("对话") == "对话"


@pytest.mark.parametrize("batch_type", ["长文本", "未分类", "未来未知分类", ""])
def test_unknown_maps_to_long_text(builder, batch_type):
    assert builder._translation_mode(batch_type) == "长文本"


def test_user_preserves_original_batch_type(builder):
    """动态 USER 保留原具体分类，不传归一化模式。"""
    msgs = builder.build_translation_prompt([_entry("k1", "Hello")], {}, "地名")
    assert "<task_category>地名</task_category>" in msgs[2]["content"]


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


# ── 旧配置兼容与 fallback ────────────────────────────────────────────────────


def test_legacy_system_migrated_to_common_system(monkeypatch):
    """旧 translation.system/user 仅作迁移输入，不把动态术语重新附加回 system。"""
    b = _builder(monkeypatch, legacy=True)
    msgs = b.build_translation_prompt([_entry("k1", "Hello")], {"Dragon": "龙"}, "人名")
    # 迁移后的 system 不含动态术语
    assert "Dragon" not in msgs[0]["content"]
    assert "mandatory_terminology" not in msgs[0]["content"]
    # 迁移后模式层与用户层仍正常工作
    assert msgs[1]["content"].strip() == "<translation_mode>实体短文本</translation_mode>"


def test_fallback_defaults_when_toml_missing(monkeypatch):
    """两个 TOML 全缺失时使用内置 fallback 常量，仍输出三层 + A/B 标记。"""
    monkeypatch.setattr(prompt_builder_mod, "_load_toml", lambda path: {})
    b = PromptBuilder("skyrim_se", "zh_CN")

    msgs = b.build_translation_prompt([_entry("k1", "Hello")], {}, "人名")
    assert [m["role"] for m in msgs] == ["system", "system", "user"]
    directives = extract_prompt_cache_directives(msgs)[1]
    assert [d["breakpoint"] for d in directives] == ["A", "B"]


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
