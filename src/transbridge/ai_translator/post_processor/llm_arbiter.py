"""
LLM裁决者。

对"模棱两可"的问题做最终判定，决定条目是接受、打回还是待审。
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import tomllib
from typing import TYPE_CHECKING, Literal
import warnings

from transbridge.config.language_profiles import load_language_profile
from transbridge.config.paths import get_data_resource_dir

from .base import output_token_limit, validate_max_output_tokens
from .prompt_contract import (
    PromptTemplateContractError,
    build_postprocess_messages,
    render_prompt_template,
    validate_prompt_template,
)

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry
    from ..llm_client import LLMClient
    from .base import PostProcessIssue
    from .llm_refiner import RefineResult
    from .polisher import PolishResult


@dataclass
class ArbiterDecision:
    """裁决结果。"""

    entry_id: str
    verdict: Literal["pass", "reject", "pending"]  # 最终裁决
    reason: str  # 裁决理由
    confidence: float  # 裁决信心度 (0-1)
    suggested_action: str  # 建议动作
    alternatives: list[str] = field(default_factory=list)  # 替代方案


@dataclass
class ArbitrationContext:
    """裁决上下文。"""

    entry: "TranslationEntry"
    original_issues: list["PostProcessIssue"] = field(default_factory=list)
    refine_result: "RefineResult | None" = None
    polish_result: "PolishResult | None" = None  # 新增：润色结果
    quality_gate_verdict: str | None = None  # 原质量关卡判定


# ── 内置默认提示词 ─────────────────────────────────────────────────────────

_DEFAULT_SYSTEM = """You are a $game_name localization quality arbiter.
Make a final quality decision on $target_lang translations of $source_lang source text.

Judge only the final candidate and return pass, reject, or pending.
Candidate priority is polished result > corrected result > initial translation. Never generate or rewrite a translation.

Decision criteria:
- "pass": publishable, natural, contextually appropriate, and free of clear errors
- "reject": a severe semantic, terminology, formatting, or style problem requires retranslation
- "pending": uncertainty, ambiguity, creative judgment, or substantial changes require human review
Prefer pending over accepting risk. Explain reject decisions and identify review points for pending.
Consider refiner and polisher confidence.

Return only the structured arbitration decision. Confidence above 0.9 means high certainty; below 0.7 suggests pending.
alternatives is especially useful for reject decisions."""

_DEFAULT_USER = """[SOURCE]
$original

[INITIAL TRANSLATION]
$initial_translation

[CORRECTED TRANSLATION]
$refined_translation

[POLISHED TRANSLATION]
$polished_translation

[CONTEXT]
$context

[DETECTED ISSUES]
$original_issues

[CORRECTION DETAILS]
$fix_details

[POLISHING DETAILS]
$polish_details

[REFINER CONFIDENCE]
$refiner_confidence

[POLISHER CONFIDENCE]
$polisher_confidence

[ORIGINAL QUALITY-GATE VERDICT]
$quality_gate_verdict

As the final arbiter, decide this entry's outcome.

Analyze whether original issues were corrected, polishing improved quality, and new problems were introduced.
Decide whether the result is publishable or needs human review.

When present, the polished translation is final.
Correction and polishing may both be present; evaluate the final result as a whole.

Return only the final arbitration decision."""

_DEFAULT_BATCH_SYSTEM = """You are a $game_name localization quality arbiter.
Make final quality decisions on multiple $target_lang translations of $source_lang source text.

Judge only final candidates and return pass, reject, or pending.
Candidate priority is polished result > corrected result > initial translation. Never generate or rewrite translations.

Decision criteria:
- "pass": publishable
- "reject": a severe problem requires retranslation
- "pending": uncertainty requires human review

Prefer pending over accepting a risky translation.

Return only the structured arbitration decisions, with one decision for every input entry. Preserve each entry ID
exactly. Confidence above 0.9 means high certainty; below 0.7 suggests pending."""


def _get_prompts_dir() -> Path:
    """定位 data/prompts/ 目录。"""

    return Path(get_data_resource_dir("prompts"))


def _load_toml(path: Path) -> dict:
    """加载 TOML 文件，失败时返回空字典。"""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        warnings.warn(f"加载 {path.name} 失败，使用内置默认提示词：{e}")
        return {}


# System 只允许稳定变量：game_name / source_lang / target_lang
_SYSTEM_ALLOWED = frozenset({"game_name", "source_lang", "target_lang"})
_SYSTEM_REQUIRED = frozenset({"game_name", "source_lang", "target_lang"})
# 单条 User 模板的 required 动态变量（含 Python 已传入的润色字段）
_ARB_USER_REQUIRED = frozenset({
    "original",
    "initial_translation",
    "refined_translation",
    "polished_translation",
    "context",
    "original_issues",
    "fix_details",
    "polish_details",
    "refiner_confidence",
    "polisher_confidence",
    "quality_gate_verdict",
})


def _resolve_system_template(name: str, template: str, fallback: str, ctx: dict) -> str:
    """校验 System 模板；违规记录 warning 并回退到内置默认模板。"""
    try:
        validate_prompt_template(
            name=name,
            template=template,
            allowed_variables=_SYSTEM_ALLOWED,
            required_variables=_SYSTEM_REQUIRED,
        )
        return template
    except PromptTemplateContractError as e:
        warnings.warn(f"{name}: {e}；使用内置默认模板")
        return fallback


def _render_system(name: str, template: str, ctx: dict) -> str:
    """严格渲染 System；失败抛 PromptTemplateContractError（进入 LLM 降级路径）。"""
    return render_prompt_template(name=name, template=template, values=ctx)


def _resolve_user_template(name: str, template: str, fallback: str, required: frozenset) -> str:
    """校验 User 模板；违规记录 warning 并回退到内置默认模板。"""
    try:
        validate_prompt_template(
            name=name,
            template=template,
            allowed_variables=frozenset({
                "game_name",
                "source_lang",
                "target_lang",
                *required,
            }),
            required_variables=required,
        )
        return template
    except PromptTemplateContractError as e:
        warnings.warn(f"{name}: {e}；使用内置默认模板")
        return fallback


class LLMArbiter:
    """
    LLM裁决者。

    职责：
    1. 对"模棱两可"的问题做最终判定
    2. 评估修复结果是否有效
    3. 决定条目是接受、打回还是待审

    裁决策略：
    - 明确的问题（如格式错误已修复）-> 直接判定
    - 质量存疑（uncertain）-> 深度分析后裁决
    - 高置信度修复 -> 接受
    - 低置信度或重大改动 -> 建议人工审核
    """

    def __init__(
        self,
        llm_client: "LLMClient",
        game_profile: str = "skyrim_se",
        target_lang: str = "zh_CN",
        strict_mode: bool = False,  # 严格模式：uncertain->reject
        max_output_tokens: int | None = None,
    ):
        """
        初始化裁决者。

        Args:
            llm_client: LLM客户端
            game_profile: 游戏配置文件名
            target_lang: 目标语言配置文件名
            strict_mode: 严格模式，uncertain时倾向reject而非pending
        """
        self._llm = llm_client
        self._strict_mode = strict_mode
        self._max_output_tokens = validate_max_output_tokens(max_output_tokens)
        self._prompts = self._load_prompts(game_profile, target_lang)

    def _load_prompts(self, game_profile: str, target_lang: str) -> dict:
        """从 TOML 文件加载提示词配置。"""
        prompts_dir = _get_prompts_dir()

        # 加载游戏和语言配置
        game_data = _load_toml(prompts_dir / "games" / f"{game_profile}.toml")
        language = load_language_profile(target_lang, prompts_dir=prompts_dir)

        # 阶段提示词与目标语言解耦；语言名称通过 ctx 在渲染时注入。
        arb_path = prompts_dir / "arbitration" / "default.toml"
        arb_data = _load_toml(arb_path)

        # 构建变量上下文
        game = game_data.get("game", {})
        ctx = {
            "game_name": game.get("name", "The Elder Scrolls V: Skyrim Special Edition (SSE)"),
            "source_lang": language.source_language,
            "target_lang": language.target_language,
        }

        arb_cfg = arb_data.get("arbitration", {})

        # System 只允许稳定变量（game_name / source_lang / target_lang）。
        system_tpl = _resolve_system_template(
            "arbitration.system",
            arb_cfg.get("system", _DEFAULT_SYSTEM),
            _DEFAULT_SYSTEM,
            ctx,
        )
        batch_system_tpl = _resolve_system_template(
            "arbitration.batch_system",
            arb_cfg.get("batch_system", _DEFAULT_BATCH_SYSTEM),
            _DEFAULT_BATCH_SYSTEM,
            ctx,
        )
        user_tpl = _resolve_user_template(
            "arbitration.user",
            arb_cfg.get("user", _DEFAULT_USER),
            _DEFAULT_USER,
            _ARB_USER_REQUIRED,
        )

        return {
            "ctx": ctx,
            "system": system_tpl,
            "system_rendered": _render_system("arbitration.system", system_tpl, ctx),
            "batch_system": batch_system_tpl,
            "batch_system_rendered": _render_system("arbitration.batch_system", batch_system_tpl, ctx),
            "user": user_tpl,
        }

    def _render_user(self, **extra) -> str:
        """严格渲染单条 User 模板；失败抛 PromptTemplateContractError。"""
        return render_prompt_template(
            name="arbitration.user",
            template=self._prompts["user"],
            values={**self._prompts["ctx"], **extra},
        )

    def arbitrate(
        self,
        ctx: ArbitrationContext,
    ) -> ArbiterDecision:
        """
        对单个条目进行裁决。

        Args:
            ctx: 裁决上下文

        Returns:
            裁决结果
        """
        # 首先尝试快速判定（无需LLM）
        quick_decision = self._quick_decide(ctx)
        if quick_decision:
            return quick_decision

        # 复杂情况使用LLM裁决
        try:
            messages = self._build_arbitration_prompt(ctx)
            response = self._llm.chat(
                messages=messages,
                max_tokens=output_token_limit(self._max_output_tokens, 1000),
            )
            return self._parse_arbitration_response(ctx.entry.id, response)
        except Exception as e:
            # LLM调用失败，基于规则做保守判定
            return self._fallback_decision(ctx, str(e))

    def arbitrate_batch(
        self,
        contexts: list[ArbitrationContext],
    ) -> dict[str, ArbiterDecision]:
        """
        批量裁决条目。

        Args:
            contexts: 裁决上下文列表

        Returns:
            entry_id -> 裁决结果的映射
        """
        if not contexts:
            return {}

        # 尝试快速判定
        decisions = {}
        needs_llm = []

        for ctx in contexts:
            quick = self._quick_decide(ctx)
            if quick:
                decisions[ctx.entry.key] = quick
            else:
                needs_llm.append(ctx)

        # 对需要LLM的条目进行批量裁决
        if needs_llm:
            try:
                messages = self._build_batch_arbitration_prompt(needs_llm)
                response = self._llm.chat(
                    messages=messages,
                    max_tokens=output_token_limit(self._max_output_tokens, 3000),
                )
                batch_decisions = self._parse_batch_arbitration_response(needs_llm, response)
                decisions.update(batch_decisions)
            except Exception as e:
                # 批量失败，逐个使用fallback
                for ctx in needs_llm:
                    decisions[ctx.entry.key] = self._fallback_decision(ctx, str(e))

        return decisions

    def _quick_decide(self, ctx: ArbitrationContext) -> ArbiterDecision | None:
        """
        快速判定：基于规则直接裁决，无需LLM。

        返回 None 表示需要LLM裁决。
        """
        entry = ctx.entry
        issues = ctx.original_issues
        refine = ctx.refine_result
        polish = ctx.polish_result

        # 情况1：无问题且无修复/润色 -> 直接通过
        if not issues and not refine and not polish:
            return ArbiterDecision(
                entry_id=entry.key,
                verdict="pass",
                reason="无检测到的问题，无需修复或润色",
                confidence=1.0,
                suggested_action="无需操作",
            )

        # 情况2：修复失败（confidence=0）-> 根据严格模式决定
        if refine and refine.confidence == 0 and refine.note.startswith("LLM修复失败"):
            if self._strict_mode:
                return ArbiterDecision(
                    entry_id=entry.key,
                    verdict="reject",
                    reason=f"修复失败: {refine.note}",
                    confidence=0.9,
                    suggested_action="打回重翻",
                )
            return ArbiterDecision(
                entry_id=entry.key,
                verdict="pending",
                reason=f"修复失败: {refine.note}，需人工处理",
                confidence=0.8,
                suggested_action="人工审核",
            )

        if polish and polish.confidence == 0 and polish.note:
            return ArbiterDecision(
                entry_id=entry.key,
                verdict="reject" if self._strict_mode else "pending",
                reason=f"润色失败: {polish.note}",
                confidence=0.0,
                suggested_action="打回重翻" if self._strict_mode else "人工审核",
            )

        # 情况3：修复信心度很高（>0.9）且无error级别问题 -> 快速通过
        error_issues = [i for i in issues if i.severity == "error"]
        if refine and not polish and refine.confidence > 0.9 and not error_issues:
            # 检查是否修复了所有问题
            fixed_types = {f.issue_type for f in refine.fixes_applied}
            remaining_issues = [i for i in issues if i.issue_type not in fixed_types]
            if not remaining_issues:
                return ArbiterDecision(
                    entry_id=entry.key,
                    verdict="pass",
                    reason=f"修复信心度高({refine.confidence:.2f})，所有问题已修复",
                    confidence=refine.confidence,
                    suggested_action="接受修复后译文",
                )

        # 情况4：修复信心度很低（<0.5）-> pending或reject
        if refine and refine.confidence < 0.5:
            if self._strict_mode:
                return ArbiterDecision(
                    entry_id=entry.key,
                    verdict="reject",
                    reason=f"修复信心度过低({refine.confidence:.2f})，存在风险",
                    confidence=0.8,
                    suggested_action="打回重翻",
                )
            else:
                return ArbiterDecision(
                    entry_id=entry.key,
                    verdict="pending",
                    reason=f"修复信心度低({refine.confidence:.2f})，需要人工确认",
                    confidence=0.7,
                    suggested_action="人工审核",
                )

        # 情况5：有严重error且未修复 -> 根据严格模式
        if error_issues:
            if self._strict_mode:
                return ArbiterDecision(
                    entry_id=entry.key,
                    verdict="reject",
                    reason=f"存在未修复的严重问题: {error_issues[0].message}",
                    confidence=0.85,
                    suggested_action="打回重翻",
                )
            # 非严格模式需要LLM判断是否可以接受

        # 需要LLM进行复杂判定
        return None

    def _fallback_decision(
        self,
        ctx: ArbitrationContext,
        error_msg: str,
    ) -> ArbiterDecision:
        """LLM失败时的降级裁决。"""
        # 保守策略：有问题就pending
        has_errors = any(i.severity == "error" for i in ctx.original_issues)

        if has_errors:
            verdict = "reject" if self._strict_mode else "pending"
        else:
            verdict = "pending"

        return ArbiterDecision(
            entry_id=ctx.entry.key,
            verdict=verdict,
            reason=f"LLM裁决失败: {error_msg}，使用保守策略",
            confidence=0.5,
            suggested_action="人工审核" if verdict == "pending" else "打回重翻",
        )

    def _build_arbitration_prompt(
        self,
        ctx: ArbitrationContext,
    ) -> list[dict]:
        """构建裁决Prompt。"""
        entry = ctx.entry
        refine = ctx.refine_result
        polish = ctx.polish_result

        # 格式化原问题
        issues_text = self._format_issues(ctx.original_issues)

        # 格式化修复和润色详情
        fix_details = self._format_fix_details(refine)
        polish_details = self._format_polish_details(polish)

        # 确定最终译文展示
        # 优先级：润色 > 修复 > 原文
        if polish and polish.polished_translation:
            final_translation = polish.polished_translation
        elif refine and refine.refined_translation:
            final_translation = refine.refined_translation
        else:
            final_translation = entry.translation or ""

        # 渲染用户Prompt（严格渲染，含润色后译文/详情/信心度）
        user_content = self._render_user(
            original=entry.original or "",
            initial_translation=entry.translation or "",
            refined_translation=refine.refined_translation if refine else entry.translation or "",
            polished_translation=polish.polished_translation if polish else final_translation,
            context=entry.context or "unknown",
            original_issues=issues_text,
            fix_details=fix_details,
            polish_details=polish_details,
            refiner_confidence=f"{refine.confidence:.2f}" if refine else "N/A",
            polisher_confidence=f"{polish.confidence:.2f}" if polish else "N/A",
            quality_gate_verdict=ctx.quality_gate_verdict or "N/A",
        )

        return build_postprocess_messages(
            stage="arbitration",
            shape="single",
            rendered_system=self._prompts["system_rendered"],
            user_content=user_content,
        )

    def _build_batch_arbitration_prompt(
        self,
        contexts: list[ArbitrationContext],
    ) -> list[dict]:
        """构建批量裁决Prompt。"""
        lines = ["Judge the final quality of the following translation entries.\n"]

        for ctx in contexts:
            entry = ctx.entry
            refine = ctx.refine_result
            polish = ctx.polish_result

            # 确定最终译文（优先级：润色 > 修复 > 原文）
            if polish and polish.polished_translation:
                final_translation = polish.polished_translation
            elif refine and refine.refined_translation:
                final_translation = refine.refined_translation
            else:
                final_translation = entry.translation or ""

            lines.append(f"\n{'=' * 60}")
            lines.append(f"【ENTRY_ID: {entry.id}】")
            lines.append(f"Source: {entry.original or ''}")
            lines.append(f"Initial translation: {entry.translation or ''}")
            lines.append(f"Corrected translation: {refine.refined_translation if refine else 'N/A'}")
            lines.append(f"Polished translation: {polish.polished_translation if polish else 'N/A'}")
            lines.append(f"Final translation: {final_translation}")
            lines.append(f"Context: {entry.context or 'unknown'}")

            if ctx.original_issues:
                lines.append("Detected issues:")
                for issue in ctx.original_issues:
                    lines.append(f"  - [{issue.severity}] {issue.issue_type}: {issue.message}")

            if refine:
                lines.append(f"Refiner confidence: {refine.confidence:.2f}")
                if refine.fixes_applied:
                    lines.append("Applied corrections:")
                    for fix in refine.fixes_applied:
                        lines.append(f"  - {fix.issue_type}: {fix.fix_description}")

            if polish:
                lines.append(f"Polisher confidence: {polish.confidence:.2f}")
                if polish.changes:
                    lines.append("Polishing changes:")
                    for change in polish.changes[:3]:  # 最多显示3个改动
                        if isinstance(change, dict):
                            aspect = change.get("aspect", "unknown")
                            lines.append(f"  - [{aspect}] {change.get('before', '')} -> {change.get('after', '')}")
                        else:
                            lines.append(f"  - [{change}]")

            lines.append(f"Original quality-gate verdict: {ctx.quality_gate_verdict or 'N/A'}")

        lines.append(f"\n{'=' * 60}")

        return build_postprocess_messages(
            stage="arbitration",
            shape="batch",
            rendered_system=self._prompts["batch_system_rendered"],
            user_content="\n".join(lines),
        )

    def _format_issues(self, issues: list["PostProcessIssue"]) -> str:
        """格式化问题列表。"""
        if not issues:
            return "none"

        lines = []
        for issue in issues:
            lines.append(f"[{issue.severity}] {issue.issue_type}: {issue.message}")
        return "\n".join(lines)

    def _format_fix_details(self, refine: "RefineResult | None") -> str:
        """格式化修复详情。"""
        if not refine:
            return "no correction"

        lines = []
        lines.append(f"Corrected translation: {refine.refined_translation}")
        lines.append(f"Refiner confidence: {refine.confidence:.2f}")

        if refine.fixes_applied:
            lines.append("Applied corrections:")
            for fix in refine.fixes_applied:
                lines.append(f"  - {fix.issue_type}: {fix.fix_description}")

        if refine.note:
            lines.append(f"Note: {refine.note}")

        return "\n".join(lines)

    def _format_polish_details(self, polish: "PolishResult | None") -> str:
        """格式化润色详情。"""
        if not polish:
            return "no polishing"

        lines = []
        lines.append(f"Polished translation: {polish.polished_translation}")
        lines.append(f"Polisher confidence: {polish.confidence:.2f}")

        if polish.changes:
            lines.append("Polishing changes:")
            for change in polish.changes:
                if isinstance(change, dict):
                    aspect = change.get("aspect", "unknown")
                    before = change.get("before", "")
                    after = change.get("after", "")
                    reason = change.get("reason", "")
                else:
                    aspect = str(change)
                    before = ""
                    after = ""
                    reason = ""
                lines.append(f"  - [{aspect}] {before} -> {after}")
                if reason:
                    lines.append(f"    Reason: {reason}")

        if polish.note:
            lines.append(f"Note: {polish.note}")

        return "\n".join(lines)

    def _parse_arbitration_response(
        self,
        entry_id: str,
        response: str,
    ) -> ArbiterDecision:
        """解析裁决响应。"""
        try:
            # 提取JSON
            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)

            verdict = data.get("verdict", "pending").lower()
            if verdict not in ("pass", "reject", "pending"):
                verdict = "pending"

            return ArbiterDecision(
                entry_id=entry_id,
                verdict=verdict,
                reason=data.get("reason", ""),
                confidence=data.get("confidence", 0.5),
                suggested_action=data.get("suggested_action", ""),
                alternatives=data.get("alternatives", []),
            )

        except (AttributeError, TypeError, json.JSONDecodeError):
            return ArbiterDecision(
                entry_id=entry_id,
                verdict="pending",
                reason=f"响应解析异常: {response[:200]}",
                confidence=0.5,
                suggested_action="建议人工确认",
            )

    def _parse_batch_arbitration_response(
        self,
        contexts: list[ArbitrationContext],
        response: str,
    ) -> dict[str, ArbiterDecision]:
        """解析批量裁决响应。"""
        context_map = {alias: ctx for ctx in contexts for alias in {str(ctx.entry.id), str(ctx.entry.key)}}
        decisions = {}
        duplicate_entry_ids: set[str] = set()

        try:
            payload = json.loads(response)
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise TypeError("arbitration batch response must contain a results array")
            data = payload["results"]

            for item in data:
                response_id = str(item.get("entry_id", ""))
                ctx = context_map.get(response_id)
                if ctx is None:
                    continue
                entry_id = str(ctx.entry.id)
                if entry_id in decisions:
                    duplicate_entry_ids.add(entry_id)
                    continue

                verdict = item.get("verdict", "pending").lower()
                if verdict not in ("pass", "reject", "pending"):
                    verdict = "pending"

                decisions[entry_id] = ArbiterDecision(
                    entry_id=entry_id,
                    verdict=verdict,
                    reason=item.get("reason", ""),
                    confidence=item.get("confidence", 0.5),
                    suggested_action=item.get("suggested_action", ""),
                    alternatives=item.get("alternatives", []),
                )

            for ctx in contexts:
                entry_id = str(ctx.entry.id)
                if entry_id in duplicate_entry_ids:
                    decisions[entry_id] = self._fallback_decision(ctx, "批量响应重复返回该条目")
                    continue
                if entry_id not in decisions:
                    decisions[entry_id] = self._fallback_decision(ctx, "批量响应缺少该条目")

        except (AttributeError, TypeError, json.JSONDecodeError):
            # JSON解析失败，所有条目使用fallback
            for ctx in contexts:
                decisions[ctx.entry.id] = self._fallback_decision(ctx, "批量响应解析失败")

        return decisions
