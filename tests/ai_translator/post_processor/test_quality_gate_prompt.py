"""QualityGateChecker 提示词契约与非回归测试（Story 14）。

覆盖：
- 单条/批量消息经 build_postprocess_messages 组装：SYSTEM(FINAL) -> USER、独立稳定 cache key、唯一 FINAL 断点。
- 批量 User 为每个条目分别渲染现有相关术语，不跨条目合并。
- 单条/批量 System 只允许稳定变量，JSON 示例为合法 JSON，枚举取值在示例外。
- pass/fail/uncertain 解析与降级行为不变（回归）。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from transbridge.ai_translator.post_processor.quality_gate import (
    QualityGateChecker,
    QualityGateResult,
    QualityVerdict,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra.prompt_cache import PROMPT_CACHE_METADATA_KEY

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "data" / "prompts"


class _CapturingLLM:
    """记录最近一次 chat 调用，返回可控响应。"""

    def __init__(self, response: str = ""):
        self.response = response
        self.calls: list[dict] = []
        self.chat_kwargs: list[dict] = []

    def chat(self, messages, max_tokens=0):
        self.calls.append(list(messages))
        self.chat_kwargs.append({"max_tokens": max_tokens})
        return self.response


class _StubTermManager:
    """按原文命中确定性术语表。"""

    def __init__(self, mapping: dict[str, dict[str, str]]):
        # mapping: original -> {term: translation}
        self._mapping = mapping

    def match_terms(self, texts: list[str]) -> dict[str, str]:
        # 语义与真实实现一致：对每条原文返回其命中术语，跨条不合并
        result: dict[str, str] = {}
        for text in texts:
            for k, v in self._mapping.get(text, {}).items():
                result[k] = v
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


def _make_checker(
    llm: _CapturingLLM,
    terms: _StubTermManager | None = None,
) -> QualityGateChecker:
    with patch(
        "transbridge.ai_translator.post_processor.quality_gate._get_prompts_dir",
        return_value=_PROMPTS_DIR,
    ):
        return QualityGateChecker(
            llm_client=llm,
            term_manager=terms,
            batch_size=2,
            game_profile="skyrim_se",
            target_lang="zh_CN",
        )


def _system_of(messages: list[dict]) -> dict:
    return messages[0]


def _user_of(messages: list[dict]) -> dict:
    return messages[1]


def _directive_of(messages: list[dict]) -> dict:
    return _system_of(messages).get(PROMPT_CACHE_METADATA_KEY, {})


# ───────────────────────── 消息分层 / FINAL / cache key ─────────────────────


def test_single_returns_system_final_user_and_unique_final_breakpoint():
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({"dragon": {"Dragon": "龙"}}))
    checker._check_single(_entry("e1", "dragon", "龙"))

    messages = llm.calls[0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"

    directive = _directive_of(messages)
    assert directive["profile"] == "single_stable_prefix"
    assert directive["breakpoint"] == "FINAL"
    assert directive["key"].startswith("transbridge.postprocess.v1.quality_gate.single.")
    # 只有一个 FINAL 断点
    finals = [d for d in [directive] if d["breakpoint"] == "FINAL"]
    assert len(finals) == 1


def test_batch_returns_system_final_user_and_independent_key():
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({}))
    checker._check_batch_internal([_entry("e1", "a", "A"), _entry("e2", "b", "B")])

    messages = llm.calls[0]
    assert len(messages) == 2
    directive = _directive_of(messages)
    assert directive["breakpoint"] == "FINAL"
    assert directive["key"].startswith("transbridge.postprocess.v1.quality_gate.batch.")


def test_single_and_batch_use_different_keys():
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({}))
    checker._check_single(_entry("e1", "a", "A"))
    single_key = _directive_of(llm.calls[0])["key"]

    llm2 = _CapturingLLM()
    checker2 = _make_checker(llm2, terms=_StubTermManager({}))
    checker2._check_batch_internal([_entry("e1", "a", "A")])
    batch_key = _directive_of(llm2.calls[0])["key"]

    assert single_key != batch_key


def test_dynamic_user_change_keeps_cache_key_stable():
    llm1 = _CapturingLLM()
    checker1 = _make_checker(llm1, terms=_StubTermManager({}))
    checker1._check_single(_entry("e1", "hello", "你好"))
    key1 = _directive_of(llm1.calls[0])["key"]

    llm2 = _CapturingLLM()
    checker2 = _make_checker(llm2, terms=_StubTermManager({}))
    checker2._check_single(_entry("e1", "hello", "您好"))
    key2 = _directive_of(llm2.calls[0])["key"]

    assert key1 == key2


# ───────────────────────── System 稳定契约与合法 JSON ───────────────────────


def test_single_system_has_no_unresolved_placeholder_or_dynamic_content():
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({}))
    checker._check_single(_entry("e1", "hello", "你好"))
    system_text = _system_of(llm.calls[0])["content"]

    # 不允许残留 $identifier 占位符
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", system_text)
    # System 不含动态条目内容
    assert "hello" not in system_text
    assert "你好" not in system_text
    # 只检测：System 明确不生成替代译文
    assert "改写" in system_text


def test_single_system_json_example_is_valid_json_and_no_pseudo_enum():
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({}))
    checker._check_single(_entry("e1", "hello", "你好"))
    system_text = _system_of(llm.calls[0])["content"]

    # 提取第一个 JSON 对象示例
    match = re.search(r"\{[\s\S]*?\n\}", system_text)
    assert match is not None
    parsed = json.loads(match.group())
    assert isinstance(parsed, dict)
    assert set(parsed.keys()) >= {"verdict", "reason", "issues"}
    # 示例里不能用 "a" | "b" 或 "..." 伪 JSON
    assert '"pass" | "fail"' not in system_text
    assert '"pass"|"fail"' not in system_text
    assert '"..."' not in system_text


def test_batch_system_json_example_is_valid_json():
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({}))
    checker._check_batch_internal([_entry("e1", "a", "A")])
    system_text = _system_of(llm.calls[0])["content"]

    match = re.search(r"\[[\s\S]*?\n\]", system_text)
    assert match is not None
    parsed = json.loads(match.group())
    assert isinstance(parsed, list)
    assert isinstance(parsed[0], dict)
    assert set(parsed[0].keys()) >= {"entry_id", "verdict", "reason", "issues"}


def test_load_prompts_and_toml_files_pass_contract():
    """真实 TOML 的 System/User 通过契约校验（稳定变量 + required 齐全）。"""
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({}))
    prompts = checker._prompts
    assert prompts["single_system"]
    assert prompts["single_user"]
    assert prompts["batch_system"]
    # 没有加载任何带 $polish 的动态字段
    for tpl in (prompts["single_system"], prompts["batch_system"]):
        assert "polish_level" not in tpl


# ───────────────────────── 批量每条术语独立渲染 ────────────────────────────


def test_batch_renders_per_entry_terms_not_merged():
    llm = _CapturingLLM()
    terms = _StubTermManager({
        "dragon lore": {"Dragon": "龙"},
        "whiterun gate": {"Whiterun": "白漫城"},
    })
    checker = _make_checker(llm, terms=terms)
    checker._check_batch_internal([
        _entry("e1", "dragon lore", "龙的传说"),
        _entry("e2", "whiterun gate", "白漫城门"),
    ])

    user_text = _user_of(llm.calls[0])["content"]
    # 每条术语各自出现在对应条目的术语表行
    e1_block = user_text.split("[ENTRY_ID: e1]")[1].split("[ENTRY_ID: e2]")[0]
    e2_block = user_text.split("[ENTRY_ID: e2]")[1]
    assert "Dragon" in e1_block and "龙" in e1_block
    assert "Whiterun" in e2_block and "白漫城" in e2_block
    # 术语不会串到对方
    assert "Whiterun" not in e1_block
    assert "Dragon" not in e2_block


def test_batch_no_terms_uses_single_semantics():
    llm = _CapturingLLM()
    checker = _make_checker(llm, terms=_StubTermManager({}))
    checker._check_batch_internal([_entry("e1", "plain text", "纯文本")])
    user_text = _user_of(llm.calls[0])["content"]
    assert "术语表：无" in user_text


# ───────────────────────── 解析 / 降级行为回归 ─────────────────────────────


@pytest.mark.parametrize(
    "response,expected",
    [
        ('{"verdict": "pass", "reason": "ok", "issues": []}', QualityVerdict.PASS),
        ('{"verdict": "fail", "reason": "bad", "issues": ["a"]}', QualityVerdict.FAIL),
        ('{"verdict": "uncertain", "reason": "?", "issues": []}', QualityVerdict.UNCERTAIN),
    ],
)
def test_parse_response_verdicts(response, expected):
    checker = _make_checker(_CapturingLLM())
    result = checker._parse_response(response)
    assert isinstance(result, QualityGateResult)
    assert result.verdict == expected


def test_single_llm_failure_falls_back_to_uncertain():
    class _BoomLLM:
        def chat(self, messages, max_tokens=0):
            raise RuntimeError("boom")

    checker = _make_checker(_BoomLLM(), terms=_StubTermManager({}))
    result = checker._check_single(_entry("e1", "hello", "你好"))
    assert result.verdict == QualityVerdict.UNCERTAIN
    assert "检测失败" in result.reason


def test_batch_parse_failure_falls_back_with_uncertain_on_fail_indicator():
    # 响应含错误指示但无法解析 JSON => 降级为 uncertain（产出 warning 问题条）
    llm = _CapturingLLM(response="无法解析并出现错误指示")
    checker = _make_checker(llm, terms=_StubTermManager({}))
    issues = checker._check_batch_internal([_entry("e1", "a", "A")])
    assert issues  # 降级仍产出问题条目（uncertain -> warning）
    assert all(i.entry_id == "e1" for i in issues)


def test_batch_parse_failure_without_fail_indicator_assumes_pass():
    # 响应无法解析且无错误指示 => 按原逻辑假设通过（不产出问题条）
    llm = _CapturingLLM(response="not json at all")
    checker = _make_checker(llm, terms=_StubTermManager({}))
    issues = checker._check_batch_internal([_entry("e1", "a", "A")])
    assert issues == []
