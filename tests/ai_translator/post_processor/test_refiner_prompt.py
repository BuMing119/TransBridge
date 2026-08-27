"""LLMRefiner 提示词契约与非回归测试（Story 14）。

覆盖：
- Refiner 只修复明确问题：System/User 不含润色职责、polish_changes、$polish_level。
- 输出字段只包含 RefineResult；不扩展数据模型。
- issues/suggestion 与现有术语完整进入 User。
- 无 issues 时不要求润色（返回当前译文语义）。
- 单条/批量经 build_postprocess_messages：SYSTEM(FINAL) -> USER、唯一 FINAL。
- RefineResult 解析与批量失败降级为单条的行为不变。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from unittest.mock import patch

from transbridge.ai_translator.post_processor.base import PostProcessIssue
from transbridge.ai_translator.post_processor.llm_refiner import (
    FixApplied,
    LLMRefiner,
    RefineResult,
)
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra.prompt_cache import PROMPT_CACHE_METADATA_KEY

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "data" / "prompts"


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


def _issue(eid: str, msg: str = "术语错误") -> PostProcessIssue:
    return PostProcessIssue(
        entry_id=eid,
        issue_type="term_mismatch",
        severity="error",
        message=msg,
        original="dragon",
        translation="龙",
        suggestion="应使用标准译法",
    )


def _make_refiner(
    llm: _CapturingLLM,
    terms: _StubTermManager | None = None,
) -> LLMRefiner:
    with patch(
        "transbridge.ai_translator.post_processor.llm_refiner._get_prompts_dir",
        return_value=_PROMPTS_DIR,
    ):
        return LLMRefiner(
            llm_client=llm,
            term_manager=terms,
            game_profile="skyrim_se",
            target_lang="zh_CN",
        )


def _system_of(messages: list[dict]) -> dict:
    return messages[0]


def _user_of(messages: list[dict]) -> dict:
    return messages[1]


def _directive_of(messages: list[dict]) -> dict:
    return _system_of(messages).get(PROMPT_CACHE_METADATA_KEY, {})


# ───────────────────────── 职责边界 / 契约 ─────────────────────────────────


def test_single_system_has_no_polish_machinery_or_polish_request():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))
    refiner.refine(_entry("e1", "dragon", "龙"), [_issue("e1")])

    system_text = _system_of(llm.calls[0])["content"]
    # 不出现润色级别 / polish 输出字段
    assert "polish_level" not in system_text
    assert "polish_changes" not in system_text
    # 未解析占位符不应存在
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", system_text)
    # 角色是问题修复者；只修改 issues 直接涉及部分，无问题时返回当前译文
    assert "correction" in system_text.lower()
    assert "only" in system_text.lower()
    assert "return the current translation unchanged" in system_text


def test_single_user_has_no_polish_level_and_includes_issues_terms():
    llm = _CapturingLLM()
    refiner = _make_refiner(
        llm,
        terms=_StubTermManager({"dragon": {"Dragon": "龙"}}),
    )
    refiner.refine(_entry("e1", "dragon", "龙"), [_issue("e1", "术语错误")])

    user_text = _user_of(llm.calls[0])["content"]
    assert "polish_level" not in user_text
    assert "polish_changes" not in user_text
    assert "术语错误" in user_text
    assert "应使用标准译法" in user_text
    assert "Dragon" in user_text and "龙" in user_text


def test_system_does_not_duplicate_native_output_schema():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))
    refiner.refine(_entry("e1", "dragon", "龙"), [_issue("e1")])

    system_text = _system_of(llm.calls[0])["content"]
    for fragment in ('"refined_translation":', '"fixes_applied":', '"confidence":', '"entry_id":'):
        assert fragment not in system_text
    assert "JSON object" not in system_text
    assert "structured correction result" in system_text


def test_toml_loads_no_polish_levels_section():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))
    prompts = refiner._prompts
    assert prompts["system"]
    assert prompts["user"]
    assert prompts["batch_system"]
    for tpl in (prompts["system"], prompts["user"], prompts["batch_system"]):
        assert "$polish_level" not in tpl
        assert "polish_changes" not in tpl
        assert "polish_level" not in tpl


# ───────────────────────── 消息分层 / FINAL / cache key ────────────────────


def test_single_uses_system_final_user_with_unique_breakpoint():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))
    refiner.refine(_entry("e1", "dragon", "龙"), [_issue("e1")])

    messages = llm.calls[0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    directive = _directive_of(messages)
    assert directive["profile"] == "single_stable_prefix"
    assert directive["breakpoint"] == "FINAL"
    assert directive["key"].startswith("transbridge.postprocess.v1.refinement.single.")


def test_batch_uses_system_final_user():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))
    refiner.refine_batch(
        [_entry("e1", "dragon", "龙"), _entry("e2", "cat", "猫")],
        {"e1": [_issue("e1")], "e2": []},
    )

    messages = llm.calls[0]
    assert len(messages) == 2
    directive = _directive_of(messages)
    assert directive["profile"] == "single_stable_prefix"
    assert directive["breakpoint"] == "FINAL"
    assert directive["key"].startswith("transbridge.postprocess.v1.refinement.batch.")


def test_single_and_batch_keys_differ():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))
    refiner.refine(_entry("e1", "dragon", "龙"), [_issue("e1")])
    single_key = _directive_of(llm.calls[0])["key"]

    llm2 = _CapturingLLM()
    refiner2 = _make_refiner(llm2, terms=_StubTermManager({}))
    refiner2.refine_batch([_entry("e1", "dragon", "龙")], {"e1": [_issue("e1")]})
    batch_key = _directive_of(llm2.calls[0])["key"]

    assert single_key != batch_key


# ───────────────────────── 无 issues 语义 / 解析 / 降级 ────────────────────


def test_no_issues_user_says_keep_unchanged_not_polish():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))
    refiner.refine(_entry("e1", "dragon", "龙"), [])

    user_text = _user_of(llm.calls[0])["content"]
    # 无检测到问题：保持当前译文，不要求润色输出 / 不使用润色级别
    assert "none (no issue detected" in user_text
    assert "polish_level" not in user_text
    assert "polish_changes" not in user_text
    # 引导模型返回当前译文而非执行润色
    assert "return the current translation unchanged" in user_text


def test_parse_refinement_response_fields_unchanged():
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_StubTermManager({}))

    response = json.dumps({
        "refined_translation": "巨龙",
        "fixes_applied": [
            {
                "issue_type": "term_mismatch",
                "original_problem": "术语错误",
                "fix_description": "改为标准译法",
            }
        ],
        "confidence": 0.9,
        "needs_arbitration": False,
        "note": "fixed",
    })
    result = refiner._parse_refinement_response(_entry("e1", "dragon", "龙"), response)
    assert isinstance(result, RefineResult)
    assert result.entry_id == "e1"
    assert result.refined_translation == "巨龙"
    assert result.original_translation == "龙"
    assert len(result.fixes_applied) == 1
    assert isinstance(result.fixes_applied[0], FixApplied)
    assert result.fixes_applied[0].fix_description == "改为标准译法"
    assert result.confidence == 0.9
    assert result.needs_arbitration is False
    assert result.note == "fixed"
    # 无 polish_changes 字段
    assert not hasattr(result, "polish_changes")


def test_batch_failure_degrades_to_single():
    class _BoomLLM:
        def chat(self, messages, max_tokens=0):
            # 仅第一次（批量）失败 => 降级为单条
            if len(getattr(self, "count", 0)) == 0:
                setattr(self, "count", [1])
                raise RuntimeError("batch boom")
            return (
                '{"refined_translation": "巨龙", "fixes_applied": [], '
                '"confidence": 0.5, "needs_arbitration": true, "note": ""}'
            )

    boom = _BoomLLM()
    refiner = _make_refiner(boom, terms=_StubTermManager({}))
    results = refiner.refine_batch(
        [_entry("e1", "dragon", "龙")],
        {"e1": [_issue("e1")]},
    )
    # 批量失败降级为单条 refine()，仍产出结果
    assert "e1" in results
    assert isinstance(results["e1"], RefineResult)


def test_batch_duplicate_result_keeps_original_for_arbitration():
    entry = _entry("e1", "Source", "Current")
    response = json.dumps({
        "results": [
            {"entry_id": "e1", "refined_translation": "First"},
            {"entry_id": "e1", "refined_translation": "Second"},
        ]
    })

    result = _make_refiner(_CapturingLLM())._parse_batch_refinement_response([entry], response)["e1"]

    assert result.refined_translation == "Current"
    assert result.needs_arbitration is True
    assert "重复" in result.note
