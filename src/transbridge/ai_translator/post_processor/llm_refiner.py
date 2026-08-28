"""
LLM修复者。

使用LLM修复检测出的明确问题。本类只负责针对性修复，不做润色优化——
润色职责已移交给 LLMPolisher（见 Story 04 / Story 14）。
"""

from collections.abc import Mapping, Set
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import tomllib
from typing import TYPE_CHECKING
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
    from ..term_database import TermDatabaseManager
    from .base import PostProcessIssue


# SYSTEM 只允许稳定变量；动态内容不得进入 System。
_SYSTEM_ALLOWED_VARIABLES = frozenset({"game_name", "source_lang", "target_lang"})
_SYSTEM_REQUIRED_VARIABLES = _SYSTEM_ALLOWED_VARIABLES
# 单条 User 允许并必需的动态变量。
_SINGLE_USER_ALLOWED_VARIABLES = frozenset({"original", "current_translation", "context", "issues", "terms"})
_SINGLE_USER_REQUIRED_VARIABLES = frozenset({"original", "current_translation", "context", "issues", "terms"})


@dataclass
class FixApplied:
    """应用的修复项。"""

    issue_type: str  # 原问题类型
    original_problem: str  # 原问题描述
    fix_description: str  # 修复方式说明


@dataclass
class RefineResult:
    """修复结果。"""

    entry_id: str
    original_translation: str
    refined_translation: str
    fixes_applied: list[FixApplied] = field(default_factory=list)
    confidence: float = 0.0  # 0-1，对修复结果的信心度
    needs_arbitration: bool = False  # 是否需要裁决（信心度低或改动大）
    note: str = ""  # 额外说明


# ── 内置默认提示词（只修复，不润色；JSON 示例为合法 JSON）─────────────────

_DEFAULT_SYSTEM = """You are a $game_name localization correction specialist.
Fix explicitly detected problems in translations from $source_lang into $target_lang.

Change only the parts directly affected by listed issues. Do not proactively polish.
If no issue was detected, return the current translation unchanged.

Correction rules:
- Preserve every source placeholder, including %s, %d, and {0}.
- Preserve every source formatting marker, including <br>, [pagebreak], and \n.
- Use the provided standard terminology.
- Do not change the source meaning.
- Close quotation marks and brackets correctly.
- Preserve the existing style and fluency.

Return only the structured correction result and no other text.
The corrected translation must equal the current translation when there is no issue. Report only corrections actually
applied; report no corrections when nothing changed. Confidence ranges from 0 to 1. Request arbitration when the
correction is uncertain. An additional note may be empty.

Fix only detected issues and do not over-rewrite.
When nothing needs correction, return the current translation unchanged and do not polish it."""

_DEFAULT_USER = """[SOURCE]
$original

[CURRENT TRANSLATION]
$current_translation

[CONTEXT]
$context

[DETECTED ISSUES]
$issues

[RELEVANT TERMINOLOGY]
$terms

Fix only detected issues and return the correction result.
If there is nothing to fix, return the current translation unchanged and do not polish it."""

_DEFAULT_BATCH_SYSTEM = """You are a $game_name localization correction specialist.
Correct explicitly detected problems across multiple translations from $source_lang into $target_lang.

For each entry, change only the parts directly affected by its listed issues. Do not proactively polish.
If an entry has no detected issue, return its current translation unchanged.

Preserve source placeholders and formatting markers, use provided terminology, and do not change source meaning.
Preserve existing style and fluency.

Return only the structured correction results and no other text. Preserve each entry ID exactly.
For every entry, the corrected translation must equal the current translation when there is no issue. Report only
corrections actually applied; report no corrections when nothing changed. Confidence ranges from 0 to 1. Request
arbitration when a correction is uncertain. An additional note may be empty.

Include every input entry in order. Fix only detected issues and do not over-rewrite.
Return unchanged translations for entries with nothing to fix."""


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


def _resolve_template(
    *,
    name: str,
    template: str,
    default: str,
    allowed_variables: Set[str],
    required_variables: Set[str],
) -> str:
    """校验候选模板契约；违规时记录 warning 并回退到内置默认模板。

    内置默认模板必须通过同一契约（开发错误不再静默回退）。
    """
    candidate = template or default
    try:
        validate_prompt_template(
            name=name,
            template=candidate,
            allowed_variables=allowed_variables,
            required_variables=required_variables,
        )
        return candidate
    except PromptTemplateContractError as e:
        warnings.warn(f"{name} 违反提示词契约，回退到内置默认模板：{e}")
        return default


class LLMRefiner:
    """
    LLM修复者。

    职责：
    1. 根据检测到的问题，使用LLM针对性修复译文
    2. 评估修复质量，标记是否需要人工裁决

    注意：只修复明确问题，不承担润色职责（润色功能已移至 LLMPolisher）。
    """

    def __init__(
        self,
        llm_client: "LLMClient",
        term_manager: "TermDatabaseManager | None" = None,
        game_profile: str = "skyrim_se",
        target_lang: str = "zh_CN",
        max_output_tokens: int | None = None,
    ):
        """
        初始化修复者。

        Args:
            llm_client: LLM客户端
            term_manager: 术语管理器（用于获取相关术语）
            game_profile: 游戏配置文件名
            target_lang: 目标语言配置文件名
        """
        self._llm = llm_client
        self._term_manager = term_manager
        self._max_output_tokens = validate_max_output_tokens(max_output_tokens)
        self._prompts = self._load_prompts(game_profile, target_lang)

    def _load_prompts(self, game_profile: str, target_lang: str) -> dict:
        """从 TOML 文件加载并校验提示词配置；违规变体回退到内置默认模板。"""
        prompts_dir = _get_prompts_dir()

        # 加载游戏和语言配置
        game_data = _load_toml(prompts_dir / "games" / f"{game_profile}.toml")
        language = load_language_profile(target_lang, prompts_dir=prompts_dir)

        # 阶段提示词与目标语言解耦；语言名称通过 ctx 在渲染时注入。
        ref_path = prompts_dir / "refinement" / "default.toml"
        ref_data = _load_toml(ref_path)

        # 构建变量上下文
        game = game_data.get("game", {})
        ctx = {
            "game_name": game.get("name", "The Elder Scrolls V: Skyrim Special Edition (SSE)"),
            "source_lang": language.source_language,
            "target_lang": language.target_language,
        }

        ref_cfg = ref_data.get("refinement", {})

        system = _resolve_template(
            name="refinement.single.system",
            template=ref_cfg.get("system", ""),
            default=_DEFAULT_SYSTEM,
            allowed_variables=_SYSTEM_ALLOWED_VARIABLES,
            required_variables=_SYSTEM_REQUIRED_VARIABLES,
        )
        user = _resolve_template(
            name="refinement.single.user",
            template=ref_cfg.get("user", ""),
            default=_DEFAULT_USER,
            allowed_variables=_SINGLE_USER_ALLOWED_VARIABLES,
            required_variables=_SINGLE_USER_REQUIRED_VARIABLES,
        )
        batch_system = _resolve_template(
            name="refinement.batch.system",
            template=ref_cfg.get("batch_system", ""),
            default=_DEFAULT_BATCH_SYSTEM,
            allowed_variables=_SYSTEM_ALLOWED_VARIABLES,
            required_variables=_SYSTEM_REQUIRED_VARIABLES,
        )

        return {
            "ctx": ctx,
            "system": system.strip(),
            "user": user.strip(),
            "batch_system": batch_system.strip(),
        }

    def _render_system(self, template: str, name: str) -> str:
        """严格渲染稳定 System（仅允许 game_name/source_lang/target_lang）。"""
        return render_prompt_template(
            name=name,
            template=template,
            values=dict(self._prompts["ctx"]),
        )

    def _render_user(self, template: str, name: str, values: Mapping[str, str]) -> str:
        """严格渲染动态 User。"""
        return render_prompt_template(name=name, template=template, values=values)

    def refine(
        self,
        entry: "TranslationEntry",
        issues: list["PostProcessIssue"],
    ) -> RefineResult:
        """
        对单个条目进行针对性修复。

        Args:
            entry: 待修复的翻译条目
            issues: 检测到的问题列表

        Returns:
            修复结果
        """
        # 构建Prompt
        messages = self._build_refinement_prompt(entry, issues)

        try:
            response = self._llm.chat(
                messages=messages,
                max_tokens=output_token_limit(self._max_output_tokens, 2000),
            )
            return self._parse_refinement_response(entry, response)
        except Exception as e:
            # LLM调用失败，返回原始译文并标记
            return RefineResult(
                entry_id=entry.id,
                original_translation=entry.translation or "",
                refined_translation=entry.translation or "",
                confidence=0.0,
                needs_arbitration=True,
                note=f"LLM修复失败: {e}",
            )

    def refine_batch(
        self,
        entries: list["TranslationEntry"],
        issues_map: dict[str, list["PostProcessIssue"]],
    ) -> dict[str, RefineResult]:
        """
        批量修复条目。

        Args:
            entries: 待修复的条目列表
            issues_map: entry_id -> 问题列表的映射

        Returns:
            entry_id -> 修复结果的映射
        """
        if not entries:
            return {}

        # 构建批量Prompt
        messages = self._build_batch_refinement_prompt(entries, issues_map)

        try:
            response = self._llm.chat(
                messages=messages,
                max_tokens=output_token_limit(self._max_output_tokens, 4000),
            )
            return self._parse_batch_refinement_response(entries, response)
        except Exception:
            # 批量失败，降级为逐个处理
            results = {}
            for entry in entries:
                entry_issues = issues_map.get(entry.id, [])
                results[entry.id] = self.refine(entry, entry_issues)
            return results

    def _build_refinement_prompt(
        self,
        entry: "TranslationEntry",
        issues: list["PostProcessIssue"],
    ) -> list[dict]:
        """构建针对性修复的Prompt。"""
        # 格式化问题列表
        issues_text = self._format_issues(issues)

        # 获取相关术语
        terms_text = self._get_relevant_terms_text(entry)

        # 渲染稳定 System 与动态 User，并组装 SYSTEM(FINAL) -> USER
        system_content = self._render_system(self._prompts["system"], "refinement.single.system")
        user_content = self._render_user(
            self._prompts["user"],
            "refinement.single.user",
            {
                "original": entry.original or "",
                "current_translation": entry.translation or "",
                "context": entry.context or "unknown",
                "issues": issues_text,
                "terms": terms_text,
            },
        )
        return build_postprocess_messages(
            stage="refinement",
            shape="single",
            rendered_system=system_content,
            user_content=user_content,
        )

    def _build_batch_refinement_prompt(
        self,
        entries: list["TranslationEntry"],
        issues_map: dict[str, list["PostProcessIssue"]],
    ) -> list[dict]:
        """构建批量修复的Prompt。"""
        lines = ["Entries to correct:"]

        for entry in entries:
            lines.append(f"\n{'=' * 60}")
            lines.append(f"【ENTRY_ID: {entry.id}】")
            lines.append(f"Source: {entry.original or ''}")
            lines.append(f"Current translation: {entry.translation or ''}")
            lines.append(f"Context: {entry.context or 'unknown'}")

            issues = issues_map.get(entry.id, [])
            if issues:
                lines.append("Detected issues:")
                for issue in issues:
                    lines.append(f"  - [{issue.severity}] {issue.issue_type}: {issue.message}")
            else:
                lines.append("Detected issues: none (keep unchanged; no correction needed)")

            # 获取该条目的相关术语
            terms = self._get_relevant_terms(entry)
            if terms:
                lines.append("Relevant terminology:")
                for term, trans in terms.items():
                    lines.append(f"  {term} → {trans}")
            else:
                lines.append("Relevant terminology: none")

        lines.append(f"\n{'=' * 60}")

        system_content = self._render_system(self._prompts["batch_system"], "refinement.batch.system")
        return build_postprocess_messages(
            stage="refinement",
            shape="batch",
            rendered_system=system_content,
            user_content="\n".join(lines),
        )

    def _format_issues(self, issues: list["PostProcessIssue"]) -> str:
        """格式化问题列表为文本。"""
        if not issues:
            return "none (no issue detected; return the current translation unchanged)"

        lines = []
        for issue in issues:
            lines.append(f"- [{issue.severity}] {issue.issue_type}")
            lines.append(f"  Issue: {issue.message}")
            if issue.suggestion:
                lines.append(f"  Suggestion: {issue.suggestion}")
        return "\n".join(lines)

    def _get_relevant_terms(self, entry: "TranslationEntry") -> dict[str, str]:
        """获取与条目相关的术语。"""
        if not self._term_manager or not entry.original:
            return {}

        # 使用术语管理器的匹配功能
        try:
            contextual = getattr(self._term_manager, "match_terms_for_entry", None)
            matched = contextual(entry) if callable(contextual) else self._term_manager.match_terms([entry.original])
            return matched
        except Exception:
            return {}

    def _get_relevant_terms_text(self, entry: "TranslationEntry") -> str:
        """获取格式化的术语文本。"""
        terms = self._get_relevant_terms(entry)
        if not terms:
            return "none"
        return "\n".join(f"  {t} → {tr}" for t, tr in terms.items())

    def _parse_refinement_response(
        self,
        entry: "TranslationEntry",
        response: str,
    ) -> RefineResult:
        """解析修复响应。"""
        try:
            # 提取JSON
            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)

            # 解析fixes_applied
            fixes = []
            for fix_data in data.get("fixes_applied", []):
                fixes.append(
                    FixApplied(
                        issue_type=fix_data.get("issue_type", ""),
                        original_problem=fix_data.get("original_problem", ""),
                        fix_description=fix_data.get("fix_description", ""),
                    )
                )

            return RefineResult(
                entry_id=entry.id,
                original_translation=entry.translation or "",
                refined_translation=data.get("refined_translation", entry.translation or ""),
                fixes_applied=fixes,
                confidence=data.get("confidence", 0.0),
                needs_arbitration=data.get("needs_arbitration", False),
                note=data.get("note", ""),
            )

        except json.JSONDecodeError:
            # JSON解析失败，尝试提取译文
            return RefineResult(
                entry_id=entry.id,
                original_translation=entry.translation or "",
                refined_translation=entry.translation or "",
                confidence=0.0,
                needs_arbitration=True,
                note=f"响应解析失败: {response[:200]}",
            )

    def _parse_batch_refinement_response(
        self,
        entries: list["TranslationEntry"],
        response: str,
    ) -> dict[str, RefineResult]:
        """解析批量修复响应。"""
        entry_map = {alias: entry for entry in entries for alias in {str(entry.id), str(entry.key)}}
        results = {}
        duplicate_entry_ids: set[str] = set()

        try:
            payload = json.loads(response)
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise TypeError("refinement batch response must contain a results array")
            data = payload["results"]

            for item in data:
                response_id = str(item.get("entry_id", ""))
                entry = entry_map.get(response_id)
                if not entry:
                    continue
                entry_id = str(entry.id)
                if entry_id in results:
                    duplicate_entry_ids.add(entry_id)
                    continue

                # 解析fixes_applied
                fixes = []
                for fix_data in item.get("fixes_applied", []):
                    fixes.append(
                        FixApplied(
                            issue_type=fix_data.get("issue_type", ""),
                            original_problem=fix_data.get("original_problem", ""),
                            fix_description=fix_data.get("fix_description", ""),
                        )
                    )

                results[entry_id] = RefineResult(
                    entry_id=entry_id,
                    original_translation=entry.translation or "",
                    refined_translation=item.get("refined_translation", entry.translation or ""),
                    fixes_applied=fixes,
                    confidence=item.get("confidence", 0.0),
                    needs_arbitration=item.get("needs_arbitration", False),
                    note=item.get("note", ""),
                )

            for entry in entries:
                if entry.id in duplicate_entry_ids:
                    results[entry.id] = RefineResult(
                        entry_id=entry.id,
                        original_translation=entry.translation or "",
                        refined_translation=entry.translation or "",
                        confidence=0.0,
                        needs_arbitration=True,
                        note="批量修复响应重复返回该条目",
                    )
                    continue
                if entry.id not in results:
                    results[entry.id] = RefineResult(
                        entry_id=entry.id,
                        original_translation=entry.translation or "",
                        refined_translation=entry.translation or "",
                        confidence=0.0,
                        needs_arbitration=True,
                        note="批量修复响应缺少该条目",
                    )

        except (AttributeError, TypeError, json.JSONDecodeError):
            # JSON解析失败，标记所有为失败
            for entry in entries:
                results[entry.id] = RefineResult(
                    entry_id=entry.id,
                    original_translation=entry.translation or "",
                    refined_translation=entry.translation or "",
                    confidence=0.0,
                    needs_arbitration=True,
                    note=f"批量响应解析失败: {response[:200]}",
                )

        return results
