"""LLMPolisher 提示词契约与非回归测试（Story 14）。

覆盖：
- single/batch System 用稳定 ctx（$game_name / $source_lang / $target_lang）严格渲染，
  不含字面量 $game_name，含实际游戏名与语言对。
- 三级别定义稳定在 System，当前选中级别只在动态 User（polish_level），不改变 cache key。
- 单条/批量经 build_postprocess_messages：SYSTEM(FINAL) -> USER、独立稳定 cache key、唯一 FINAL。
- 术语内容与遍历顺序不变；PolishResult 解析与失败保留原译文行为不变（回归）。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from unittest.mock import patch

from transbridge.ai_translator.post_processor.polisher import LLMPolisher, PolishResult
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra.prompt_cache import PROMPT_CACHE_METADATA_KEY

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "data" / "prompts"

_GAME_NAME = "上古卷轴5：天际特别版（SSE）"
_SOURCE_LANG = "英文"
_TARGET_LANG = "中文"


class _CapturingLLM:
    def __init__(self, response: str = ""):
        self.response = response
        self.calls: list[dict] = []

    def chat(self, messages, max_tokens=0):
        self.calls.append(list(messages))
        return self.response


class _StubTermManager:
    def __init__(self, mapping: dict[str, dict[str, str]]):
        self._mapping = mapping

    def match_terms(self, texts: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for text in texts:
            result.update(self._mapping.get(text, {}))
        return result


def _entry(eid: str, original: str, translation: str, context: str | None = "NPC_:FULL") -> TranslationEntry:
    return TranslationEntry(
        id=eid,
        key=eid,
        original=original,
        translation=translation,
        stage=1,
        context=context,
    )


def _make_polisher(
    llm: _CapturingLLM,
    terms: _StubTermManager | None = None,
    polish_level: str = "moderate",
) -> LLMPolisher:
    with patch(
        "transbridge.ai_translator.post_processor.polisher._get_prompts_dir",
        return_value=_PROMPTS_DIR,
    ):
        return LLMPolisher(
            llm_client=llm,
            term_manager=terms,
            game_profile="skyrim_se",
            target_lang="zh_CN",
            polish_level=polish_level,
        )


def _system_of(messages: list[dict]) -> dict:
    return messages[0]


def _user_of(messages: list[dict]) -> dict:
    return messages[1]


def _directive_of(messages: list[dict]) -> dict:
    return _system_of(messages).get(PROMPT_CACHE_METADATA_KEY, {})


# ───────────────────────── System 稳定渲染 ─────────────────────────────────


def test_single_system_renders_game_and_langs_no_literal_dollar():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))
    polisher.polish(_entry("e1", "dragon", "龙"))

    system_text = _system_of(llm.calls[0])["content"]
    assert "$game_name" not in system_text
    assert "$source_lang" not in system_text
    assert "$target_lang" not in system_text
    # 无任何未解析占位符
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", system_text)
    assert _GAME_NAME in system_text
    assert _SOURCE_LANG in system_text
    assert _TARGET_LANG in system_text


def test_batch_system_renders_game_and_langs():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))
    polisher.polish_batch([_entry("e1", "dragon", "龙")])

    system_text = _system_of(llm.calls[0])["content"]
    assert "$game_name" not in system_text
    assert "$source_lang" not in system_text
    assert "$target_lang" not in system_text
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", system_text)
    assert _GAME_NAME in system_text
    assert _SOURCE_LANG in system_text
    assert _TARGET_LANG in system_text


def test_level_definitions_stable_in_system():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))
    polisher.polish(_entry("e1", "dragon", "龙"))
    system_text = _system_of(llm.calls[0])["content"]
    # 三种级别定义稳定在 System
    assert "light" in system_text
    assert "moderate" in system_text
    assert "aggressive" in system_text


# ───────────────────────── 动态 User 与 cache key ─────────────────────────


def test_single_user_has_required_dynamic_fields():
    llm = _CapturingLLM()
    polisher = _make_polisher(
        llm,
        terms=_StubTermManager({"dragon": {"Dragon": "龙"}}),
        polish_level="aggressive",
    )
    polisher.polish(_entry("e1", "dragon", "龙"))

    user_text = _user_of(llm.calls[0])["content"]
    assert "dragon" in user_text
    assert "龙" in user_text
    assert "NPC_:FULL" in user_text
    assert "Dragon" in user_text
    # 当前选中级别（aggressive 描述）进入动态 User
    assert "深度润色" in user_text


def test_terms_content_and_order_preserved():
    llm = _CapturingLLM()
    polisher = _make_polisher(
        llm,
        terms=_StubTermManager({"dragon": {"Dragon": "龙", "Alduin": "奥杜因"}}),
    )
    polisher.polish(_entry("e1", "dragon", "龙"))

    user_text = _user_of(llm.calls[0])["content"]
    # 两个术语按 dict 遍历顺序出现
    assert user_text.index("Dragon") < user_text.index("Alduin")
    assert "Dragon → 龙" in user_text
    assert "Alduin → 奥杜因" in user_text


def test_polish_level_change_does_not_change_cache_key():
    llm_a = _CapturingLLM()
    polisher_a = _make_polisher(llm_a, terms=_StubTermManager({}), polish_level="light")
    polisher_a.polish(_entry("e1", "dragon", "龙"))
    key_light = _directive_of(llm_a.calls[0])["key"]

    llm_b = _CapturingLLM()
    polisher_b = _make_polisher(llm_b, terms=_StubTermManager({}), polish_level="aggressive")
    polisher_b.polish(_entry("e1", "dragon", "龙"))
    key_aggressive = _directive_of(llm_b.calls[0])["key"]

    assert key_light == key_aggressive


def test_single_and_batch_keys_differ():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))
    polisher.polish(_entry("e1", "dragon", "龙"))
    single_key = _directive_of(llm.calls[0])["key"]
    assert single_key.startswith("transbridge.postprocess.v1.polish.single.")

    llm2 = _CapturingLLM()
    polisher2 = _make_polisher(llm2, terms=_StubTermManager({}))
    polisher2.polish_batch([_entry("e1", "dragon", "龙")])
    batch_key = _directive_of(llm2.calls[0])["key"]
    assert batch_key.startswith("transbridge.postprocess.v1.polish.batch.")

    assert single_key != batch_key


# ───────────────────────── 消息分层 / FINAL ────────────────────────────────


def test_single_system_final_user_with_unique_breakpoint():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))
    polisher.polish(_entry("e1", "dragon", "龙"))

    messages = llm.calls[0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    directive = _directive_of(messages)
    assert directive["profile"] == "single_stable_prefix"
    assert directive["breakpoint"] == "FINAL"


def test_batch_system_final_user():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))
    polisher.polish_batch([_entry("e1", "dragon", "龙")])

    messages = llm.calls[0]
    assert len(messages) == 2
    directive = _directive_of(messages)
    assert directive["profile"] == "single_stable_prefix"
    assert directive["breakpoint"] == "FINAL"


def test_namespace_keys_do_not_match_translation_profile():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))
    polisher.polish(_entry("e1", "dragon", "龙"))
    directive = _directive_of(llm.calls[0])
    assert directive["profile"] == "single_stable_prefix"
    # 不是翻译层 A/B
    assert directive["breakpoint"] != "A"
    assert directive["breakpoint"] != "B"


# ───────────────────────── 回归：解析与失败降级 ────────────────────────────


def test_parse_polish_response_fields_unchanged():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))

    response = json.dumps({
        "polished_translation": "勇者与巨龙",
        "changes": [{"aspect": "fluency", "before": "A", "after": "B", "reason": "更流畅"}],
        "confidence": 0.85,
        "needs_arbitration": False,
        "note": "polished",
    })
    result = polisher._parse_polish_response(_entry("e1", "dragon", "龙"), response)
    assert isinstance(result, PolishResult)
    assert result.entry_id == "e1"
    assert result.polished_translation == "勇者与巨龙"
    assert result.original_translation == "龙"
    assert result.confidence == 0.85
    assert result.needs_arbitration is False
    assert result.note == "polished"


def test_parse_polish_response_normalizes_string_change_labels():
    llm = _CapturingLLM()
    polisher = _make_polisher(llm, terms=_StubTermManager({}))

    response = json.dumps({
        "polished_translation": "勇者与巨龙",
        "changes": ["style", "fluency"],
        "confidence": 0.85,
    })

    result = polisher._parse_polish_response(_entry("e1", "dragon", "龙"), response)

    assert result.changes == [
        {"aspect": "style", "before": "", "after": "", "reason": ""},
        {"aspect": "fluency", "before": "", "after": "", "reason": ""},
    ]


def test_llm_failure_returns_original_translation():
    class _BoomLLM:
        def chat(self, messages, max_tokens=0):
            raise RuntimeError("polish boom")

    polisher = _make_polisher(_BoomLLM(), terms=_StubTermManager({}))
    result = polisher.polish(_entry("e1", "dragon", "龙"))
    assert isinstance(result, PolishResult)
    assert result.polished_translation == "龙"
    assert result.original_translation == "龙"
    assert result.needs_arbitration is True
    assert "LLM润色失败" in result.note


def test_llm_failure_batch_returns_original_translation():
    class _BoomLLM:
        def chat(self, messages, max_tokens=0):
            raise RuntimeError("batch boom")

    polisher = _make_polisher(_BoomLLM(), terms=_StubTermManager({}))
    results = polisher.polish_batch([_entry("e1", "dragon", "龙"), _entry("e2", "cat", "猫")])
    assert set(results.keys()) == {"e1", "e2"}
    for rid, result in results.items():
        assert isinstance(result, PolishResult)
        assert result.polished_translation in ("龙", "猫")
        assert result.needs_arbitration is True
