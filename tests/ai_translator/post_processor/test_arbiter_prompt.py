"""LLMArbiter 提示词契约与非回归测试（Story 14）。

覆盖：
- 单条 User 实际引用润色后译文 / 润色详情 / 润色者信心度（polished_translation /
  polish_details / polisher_confidence），不依赖内置 fallback。
- 单条无 PolishResult 时使用现有 N/A / fallback 语义。
- 批量最终候选优先级保持：润色 > 修复 > 初始。
- System 只裁决最终候选，不生成新译文。
- 单条/批量经 build_postprocess_messages：SYSTEM(FINAL) -> USER、独立 cache key。
- Python 快速裁决路径与 strict mode 不受提示词重构影响（回归）。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from unittest.mock import patch

from transbridge.ai_translator.post_processor.llm_arbiter import (
    ArbitrationContext,
    LLMArbiter,
)
from transbridge.ai_translator.post_processor.llm_refiner import RefineResult
from transbridge.ai_translator.post_processor.polisher import PolishResult
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.infra.prompt_cache import PROMPT_CACHE_METADATA_KEY

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _REPO_ROOT / "data" / "prompts"

_GAME_NAME = "The Elder Scrolls V: Skyrim Special Edition (SSE)"
_SOURCE_LANG = "English"
_TARGET_LANG = "Simplified Chinese"

# 变量不被 string.Template 当作占位符（值中包含真实的 $，用于无 PolishResult 时
# 验证 polished_translation 仍为回退的最终译文，而不是被误解析）。
_INITIAL = "初始译文"
_REFINED = "修复后译文"
_POLISHED = "润色后译文"


class _CapturingLLM:
    def __init__(self, response: str = ""):
        self.response = response
        self.calls: list[dict] = []

    def chat(self, messages, max_tokens=0):
        self.calls.append(list(messages))
        return self.response


def _entry(eid: str, original: str, translation: str, context: str | None = "NPC_:FULL") -> TranslationEntry:
    return TranslationEntry(
        id=eid,
        key=eid,
        original=original,
        translation=translation,
        stage=1,
        context=context,
    )


def _make_arbiter(llm: _CapturingLLM, strict_mode: bool = False) -> LLMArbiter:
    with patch(
        "transbridge.ai_translator.post_processor.llm_arbiter._get_prompts_dir",
        return_value=_PROMPTS_DIR,
    ):
        return LLMArbiter(
            llm_client=llm,
            game_profile="skyrim_se",
            target_lang="zh_CN",
            strict_mode=strict_mode,
        )


def _system_of(messages: list[dict]) -> dict:
    return messages[0]


def _user_of(messages: list[dict]) -> dict:
    return messages[1]


def _directive_of(messages: list[dict]) -> dict:
    return _system_of(messages).get(PROMPT_CACHE_METADATA_KEY, {})


def _ctx(
    *,
    entry: TranslationEntry,
    refine: RefineResult | None = None,
    polish: PolishResult | None = None,
) -> ArbitrationContext:
    return ArbitrationContext(
        entry=entry,
        refine_result=refine,
        polish_result=polish,
        quality_gate_verdict="pass",
    )


def _polish_result(eid: str) -> PolishResult:
    return PolishResult(
        entry_id=eid,
        original_translation=_INITIAL,
        polished_translation=_POLISHED,
        changes=[{"aspect": "fluency", "before": "A", "after": _POLISHED, "reason": "更流畅"}],
        confidence=0.95,
        needs_arbitration=False,
        note="润色完成",
    )


def _refine_result(eid: str) -> RefineResult:
    return RefineResult(
        entry_id=eid,
        original_translation=_INITIAL,
        refined_translation=_REFINED,
        confidence=0.9,
        note="已修复",
    )


# ───────────────────────── System 只裁决不重写 ─────────────────────────────


def test_system_only_arbitrates_no_rewrite_and_renders_game():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), polish=_polish_result("e1"))
    messages = arbiter._build_arbitration_prompt(ctx)

    system_text = _system_of(messages)["content"]
    # 游戏/语言对进入 System，无未解析占位符
    assert "$game_name" not in system_text
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", system_text)
    assert _GAME_NAME in system_text
    assert _SOURCE_LANG in system_text
    assert _TARGET_LANG in system_text
    # 只裁决不重写
    assert "pass" in system_text and "reject" in system_text and "pending" in system_text
    assert "Never generate" in system_text


# ───────────────────────── 单条 User 引用润色字段 ─────────────────────────


def test_single_user_references_polish_fields_when_polish_result_present():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), polish=_polish_result("e1"))
    messages = arbiter._build_arbitration_prompt(ctx)

    user_text = _user_of(messages)["content"]
    assert _POLISHED in user_text  # polished_translation
    assert "[POLISHED TRANSLATION]" in user_text
    assert "润色完成" in user_text  # polish_details（含 note）
    assert "0.95" in user_text  # polisher_confidence


def test_single_user_uses_na_fallback_when_no_polish_result():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    # 无润色、无修复 -> polished_translation 回退为初始译文（无 PolishResult）
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL))
    messages = arbiter._build_arbitration_prompt(ctx)

    user_text = _user_of(messages)["content"]
    # 无 PolishResult：润色详情与信心度为 N/A/无润色语义
    assert "no polishing" in user_text
    assert "N/A" in user_text
    # 不残留任何未渲染占位符
    assert not re.search(r"\$[A-Za-z_][A-Za-z0-9_]*", user_text)


def test_single_user_reflected_translation_priority():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(
        entry=_entry("e1", "dragon", _INITIAL),
        refine=_refine_result("e1"),
        polish=_polish_result("e1"),
    )
    messages = arbiter._build_arbitration_prompt(ctx)

    user_text = _user_of(messages)["content"]
    assert _REFINED in user_text
    assert _POLISHED in user_text
    assert _INITIAL in user_text


# ───────────────────────── 批量最终候选优先级 ─────────────────────────────


def test_batch_final_candidate_priority_polish_over_refine():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(
        entry=_entry("e1", "dragon", _INITIAL),
        refine=_refine_result("e1"),
        polish=_polish_result("e1"),
    )
    messages = arbiter._build_batch_arbitration_prompt([ctx])

    user_text = _user_of(messages)["content"]
    # 最终译文优先级：润色 > 修复 > 初始
    assert "Final translation:" in user_text
    final_line = [line for line in user_text.splitlines() if line.startswith("Final translation:")][0]
    assert _POLISHED in final_line


def test_batch_final_candidate_falls_back_to_refine_without_polish():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    # 无润色 -> 最终 = 修复结果
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), refine=_refine_result("e1"))
    messages = arbiter._build_batch_arbitration_prompt([ctx])

    user_text = _user_of(messages)["content"]
    final_line = [line for line in user_text.splitlines() if line.startswith("Final translation:")][0]
    assert _REFINED in final_line
    assert "N/A" in user_text  # 润色后译文 N/A


# ───────────────────────── 消息分层 / FINAL / key ─────────────────────────


def test_single_system_final_user_with_unique_breakpoint():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), polish=_polish_result("e1"))
    messages = arbiter._build_arbitration_prompt(ctx)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    directive = _directive_of(messages)
    assert directive["profile"] == "single_stable_prefix"
    assert directive["breakpoint"] == "FINAL"
    assert directive["key"].startswith("transbridge.postprocess.v1.arbitration.single.")


def test_batch_system_final_user():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), polish=_polish_result("e1"))
    messages = arbiter._build_batch_arbitration_prompt([ctx])

    assert len(messages) == 2
    directive = _directive_of(messages)
    assert directive["profile"] == "single_stable_prefix"
    assert directive["breakpoint"] == "FINAL"
    assert directive["key"].startswith("transbridge.postprocess.v1.arbitration.batch.")


def test_single_and_batch_keys_differ():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), polish=_polish_result("e1"))
    single_key = _directive_of(arbiter._build_arbitration_prompt(ctx))["key"]
    batch_key = _directive_of(arbiter._build_batch_arbitration_prompt([ctx]))["key"]
    assert single_key != batch_key


# ───────────────────────── 回归：快裁与 strict mode ───────────────────────


def test_quick_decide_unchanged_no_issues_no_llm_call():
    class _NeverCalledLLM:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, max_tokens=0):
            self.calls += 1
            return "{}"

    llm = _NeverCalledLLM()
    arbiter = _make_arbiter(llm)
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL))
    decision = arbiter.arbitrate(ctx)
    assert decision.verdict == "pass"
    assert llm.calls == 0


def test_quick_decide_low_refine_confidence_strict_reject():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm, strict_mode=True)
    low_refine = RefineResult(
        entry_id="e1",
        original_translation=_INITIAL,
        refined_translation=_REFINED,
        confidence=0.3,
        note="低信心度",
    )
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), refine=low_refine)
    decision = arbiter.arbitrate(ctx)
    assert decision.verdict == "reject"
    assert "信心度过低" in decision.reason


def test_quick_decide_low_refine_confidence_non_strict_pending():
    llm = _CapturingLLM()
    arbiter = _make_arbiter(llm, strict_mode=False)
    low_refine = RefineResult(
        entry_id="e1",
        original_translation=_INITIAL,
        refined_translation=_REFINED,
        confidence=0.3,
        note="低信心度",
    )
    ctx = _ctx(entry=_entry("e1", "dragon", _INITIAL), refine=low_refine)
    decision = arbiter.arbitrate(ctx)
    assert decision.verdict == "pending"
    assert "信心度低" in decision.reason


def test_batch_arbitration_tolerates_legacy_string_changes():
    llm = _CapturingLLM(
        '{"results":[{"entry_id":"e1","verdict":"pass","reason":"ok","confidence":0.9,"suggested_action":"accept"}]}'
    )
    arbiter = _make_arbiter(llm)
    polish = _polish_result("e1")
    polish.changes = ["style", "fluency"]
    context = _ctx(entry=_entry("e1", "dragon", _INITIAL), polish=polish)

    decisions = arbiter.arbitrate_batch([context])

    assert decisions["e1"].verdict == "pass"
    assert len(llm.calls) == 1
    assert "[style]" in _user_of(llm.calls[0])["content"]


def test_batch_duplicate_result_falls_back_to_pending():
    context = _ctx(entry=_entry("e1", "Source", "Current"))
    response = json.dumps({
        "results": [
            {"entry_id": "e1", "verdict": "pass", "reason": "ok", "confidence": 0.9},
            {"entry_id": "e1", "verdict": "pass", "reason": "duplicate", "confidence": 0.9},
        ]
    })

    decision = _make_arbiter(_CapturingLLM())._parse_batch_arbitration_response([context], response)["e1"]

    assert decision.verdict == "pending"
    assert "重复" in decision.reason
