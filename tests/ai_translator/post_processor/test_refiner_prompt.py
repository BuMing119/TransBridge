"""LLMRefiner 提示词契约与非回归测试（Story 14）。

覆盖：
- Refiner 只修复明确问题：System/User 不含润色职责、polish_changes、$polish_level。
- Structured Outputs 合同保持不变；本地 RefineResult 额外记录兼容的有效性和失败分类。
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
from transbridge.ai_translator.post_processor.checkpoint import PostProcessCheckpoint
from transbridge.ai_translator.post_processor.llm_refiner import (
    FixApplied,
    LLMRefiner,
    RefineResult,
)
from transbridge.application.io import EntryKey, SourceNamespace
from transbridge.application.translation.ai_request_budget import AiRequestCancelledError
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
    assert result.valid is False
    assert result.failure_code == "invalid_response"
    assert "重复" in result.note


def test_explicit_single_terms_and_structured_issue_fields_bypass_term_manager():
    class _UnexpectedTermManager:
        def match_terms_for_entry(self, entry):
            raise AssertionError("explicit scoped terms must be authoritative")

    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_UnexpectedTermManager())
    issue = PostProcessIssue(
        entry_id="e1",
        issue_type=PostProcessIssue.TERM_MISMATCH,
        severity="warning",
        message="remaining deterministic mismatch",
        original="Greybeard",
        translation="灰胡子",
        term="Greybeards",
        matched_form="Greybeard",
        standard_translation="灰胡子长老",
    )

    refiner.refine(_entry("e1", "Greybeard", "灰胡子"), [issue], terms={"Greybeards": "灰胡子长老"})

    user_text = _user_of(llm.calls[0])["content"]
    assert "Term: Greybeards" in user_text
    assert "Matched form: Greybeard" in user_text
    assert "Required translation: 灰胡子长老" in user_text
    assert "Greybeards → 灰胡子长老" in user_text


def test_explicit_batch_terms_are_isolated_by_stable_entry_key():
    class _UnexpectedTermManager:
        def match_terms_for_entry(self, entry):
            raise AssertionError("explicit scoped terms must be authoritative")

    first = TranslationEntry(
        id="legacy-one",
        key="stable-one",
        original="Dragon",
        translation="龙",
        stage=1,
        context="NPC_:FULL",
    )
    second = TranslationEntry(
        id="legacy-two",
        key="stable-two",
        original="Whiterun",
        translation="白城",
        stage=1,
        context="NPC_:FULL",
    )
    llm = _CapturingLLM()
    refiner = _make_refiner(llm, terms=_UnexpectedTermManager())

    refiner.refine_batch(
        [first, second],
        {first.id: [_issue(first.id)], second.id: [_issue(second.id)]},
        terms_map={"stable-one": {"Dragon": "巨龙"}, "stable-two": {"Whiterun": "雪漫城"}},
    )

    user_text = _user_of(llm.calls[0])["content"]
    first_block, second_block = user_text.split("【ENTRY_ID: legacy-two】")
    assert "Dragon → 巨龙" in first_block
    assert "Whiterun → 雪漫城" not in first_block
    assert "Whiterun → 雪漫城" in second_block
    assert "Dragon → 巨龙" not in second_block


def test_batch_call_failure_single_fallback_preserves_explicit_terms():
    class _BatchThenSingleLLM:
        def __init__(self):
            self.calls = []

        def chat(self, messages, max_tokens=0):
            self.calls.append(messages)
            if len(self.calls) == 1:
                raise RuntimeError("batch failed")
            return json.dumps({
                "refined_translation": "巨龙",
                "fixes_applied": [],
                "confidence": 1.0,
                "needs_arbitration": False,
                "note": "",
            })

    class _UnexpectedTermManager:
        def match_terms_for_entry(self, entry):
            raise AssertionError("fallback must preserve explicit terms")

    llm = _BatchThenSingleLLM()
    entry = _entry("e1", "Dragon", "龙")
    result = _make_refiner(llm, terms=_UnexpectedTermManager()).refine_batch(
        [entry],
        {entry.id: [_issue(entry.id)]},
        terms_map={entry.key: {"Dragon": "巨龙"}},
    )[entry.id]

    assert result.valid is True
    assert result.refined_translation == "巨龙"
    assert "Dragon → 巨龙" in _user_of(llm.calls[1])["content"]


def test_single_empty_missing_and_invalid_json_responses_are_invalid():
    refiner = _make_refiner(_CapturingLLM())
    entry = _entry("e1", "Dragon", "龙")

    for response in ('{"refined_translation": ""}', "{}", "not json"):
        result = refiner._parse_refinement_response(entry, response)
        assert result.valid is False
        assert result.failure_code == "invalid_response"
        assert result.refined_translation == "龙"
        assert result.needs_arbitration is True


def test_batch_unknown_entry_invalidates_all_requested_results():
    entries = [_entry("e1", "Dragon", "龙"), _entry("e2", "Cat", "猫")]
    response = json.dumps({
        "results": [
            {"entry_id": "e1", "refined_translation": "巨龙"},
            {"entry_id": "e2", "refined_translation": "猫咪"},
            {"entry_id": "unknown", "refined_translation": "?"},
        ]
    })

    results = _make_refiner(_CapturingLLM())._parse_batch_refinement_response(entries, response)

    assert set(results) == {"e1", "e2"}
    assert all(result.valid is False for result in results.values())
    assert all(result.failure_code == "invalid_response" for result in results.values())
    assert all(result.refined_translation in {"龙", "猫"} for result in results.values())
    assert all("unknown" in result.note for result in results.values())


def test_ambiguous_legacy_key_alias_cannot_cross_stable_entry_keys():
    entries = [
        TranslationEntry(
            id="project-id",
            key="shared",
            original="Dragon",
            translation="项目旧译",
            stage=1,
            context="NPC_:FULL",
            entry_key=EntryKey(SourceNamespace("project"), "shared"),
        ),
        TranslationEntry(
            id="plugin-id",
            key="shared",
            original="Dragon",
            translation="插件旧译",
            stage=1,
            context="NPC_:FULL",
            entry_key=EntryKey(SourceNamespace("plugin"), "shared"),
        ),
    ]
    response = json.dumps({"results": [{"entry_id": "shared", "refined_translation": "歧义结果"}]})

    results = _make_refiner(_CapturingLLM())._parse_batch_refinement_response(entries, response)

    assert set(results) == {"project-id", "plugin-id"}
    assert all(result.valid is False for result in results.values())
    assert {result.refined_translation for result in results.values()} == {"项目旧译", "插件旧译"}


def test_batch_empty_missing_and_invalid_json_responses_are_invalid():
    entries = [_entry("e1", "Dragon", "龙"), _entry("e2", "Cat", "猫")]
    refiner = _make_refiner(_CapturingLLM())

    empty = refiner._parse_batch_refinement_response(
        entries,
        '{"results":[{"entry_id":"e1","refined_translation":"   "}]}',
    )
    malformed = refiner._parse_batch_refinement_response(entries, "not json")

    assert empty["e1"].valid is False
    assert empty["e1"].failure_code == "invalid_response"
    assert empty["e1"].needs_arbitration is True
    assert "空值" in empty["e1"].note
    assert empty["e2"].valid is False
    assert "缺少" in empty["e2"].note
    assert all(result.valid is False for result in malformed.values())
    assert all(result.failure_code == "invalid_response" for result in malformed.values())


def test_checkpoint_round_trips_structured_issue_and_refine_validity_with_legacy_defaults():
    issue = _issue("e1")
    issue.term = "Dragon"
    issue.matched_form = "Dragons"
    issue.standard_translation = "巨龙"
    restored_issue = PostProcessCheckpoint.issue_from_dict(PostProcessCheckpoint.issue_to_dict(issue))
    restored_result = PostProcessCheckpoint.refine_result_from_dict({
        "entry_id": "e1",
        "original_translation": "龙",
        "refined_translation": "龙",
        "valid": False,
        "failure_code": "invalid_response",
    })
    legacy_result = PostProcessCheckpoint.refine_result_from_dict({
        "entry_id": "e1",
        "original_translation": "龙",
        "refined_translation": "巨龙",
    })

    assert (restored_issue.term, restored_issue.matched_form, restored_issue.standard_translation) == (
        "Dragon",
        "Dragons",
        "巨龙",
    )
    assert restored_result.valid is False
    assert restored_result.failure_code == "invalid_response"
    assert legacy_result.valid is True
    assert legacy_result.failure_code == ""


def test_single_call_failure_and_cancellation_have_structured_failure_codes():
    class _FailingLLM:
        def __init__(self, error):
            self._error = error

        def chat(self, messages, max_tokens=0):
            raise self._error

    entry = _entry("e1", "Dragon", "龙")
    failed = _make_refiner(_FailingLLM(RuntimeError("provider unavailable"))).refine(entry, [_issue("e1")])
    cancelled = _make_refiner(_FailingLLM(AiRequestCancelledError("cancelled"))).refine(entry, [_issue("e1")])

    assert failed.valid is False
    assert failed.failure_code == "call_failed"
    assert cancelled.valid is False
    assert cancelled.failure_code == "cancelled"


def test_batch_cancellation_does_not_fallback_to_single_calls():
    class _CancelledLLM:
        def __init__(self):
            self.call_count = 0

        def chat(self, messages, max_tokens=0):
            self.call_count += 1
            raise AiRequestCancelledError("cancelled")

    llm = _CancelledLLM()
    entries = [_entry("e1", "Dragon", "龙"), _entry("e2", "Cat", "猫")]
    results = _make_refiner(llm).refine_batch(entries, {entry.id: [_issue(entry.id)] for entry in entries})

    assert llm.call_count == 1
    assert all(result.valid is False for result in results.values())
    assert all(result.failure_code == "cancelled" for result in results.values())
