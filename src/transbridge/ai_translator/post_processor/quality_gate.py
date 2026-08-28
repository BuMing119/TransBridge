"""
质量关卡检测器：调用LLM判断翻译是否存在明显质量问题。

检测逻辑（由LLM判断，只检测不改写）：
- 有大问题 → 标记为失败（建议打回重翻）
- 无大问题 → 通过
- 无法判定 → 标记为待定（需人工审核）

提示词配置从 data/prompts/quality_gate/default.toml 加载，语言名称由 langs/{target_lang}.toml 注入。
模板经 prompt_contract 严格校验与渲染；违规变体回退到内置默认模板；消息经
build_postprocess_messages 组装为 SYSTEM(FINAL) -> USER 并计算阶段独立 cache key。
"""

from collections.abc import Mapping, Set
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import re
import tomllib
from typing import TYPE_CHECKING
import warnings

from transbridge.config.language_profiles import load_language_profile
from transbridge.config.paths import get_data_resource_dir

from .base import (
    BaseChecker,
    PostProcessIssue,
    output_token_limit,
    validate_max_output_tokens,
)
from .prompt_contract import (
    PromptTemplateContractError,
    build_postprocess_messages,
    render_prompt_template,
    validate_prompt_template,
)

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry
    from ..llm_client import LLMClient


# SYSTEM 只允许稳定变量；动态内容不得进入 System。
_SYSTEM_ALLOWED_VARIABLES = frozenset({"game_name", "source_lang", "target_lang"})
_SYSTEM_REQUIRED_VARIABLES = _SYSTEM_ALLOWED_VARIABLES
# 单条 User 允许并必需的动态变量。
_SINGLE_USER_ALLOWED_VARIABLES = frozenset({"original", "translation", "context", "terms"})
_SINGLE_USER_REQUIRED_VARIABLES = frozenset({"original", "translation", "context", "terms"})


class QualityVerdict(Enum):
    """质量判定结果。"""

    PASS = "pass"  # 通过，无明显问题
    FAIL = "fail"  # 失败，有明显质量问题（建议打回）
    UNCERTAIN = "uncertain"  # 无法判定（需人工审核）


@dataclass
class QualityGateResult:
    """LLM返回的质量判定结果。"""

    verdict: QualityVerdict
    reason: str  # 判定理由
    issues: list[str]  # 发现的具体问题列表


# ── 内置默认值（文件缺失或契约违规时使用）──────────────────────────────────

_DEFAULT_SINGLE_SYSTEM = (
    "You are a $game_name localization quality inspector. Determine whether a translation from $source_lang meets "
    "$target_lang quality standards.\n\n"
    """Only inspect and judge the translation. Never generate or rewrite it.
Determine only whether it has clear quality problems.

Return only the structured quality decision and no other text.

verdict must be exactly one of: "pass", "fail", or "uncertain".
- "pass": accurate and complete, with no clear problem
- "fail": a clear error exists and retranslation is recommended
- "uncertain": quality is questionable but the translation is not clearly wrong; human review is required

Always return "fail" for unchanged source echo (except code, identifiers, or numbers) or runaway repetition.

Focus on clear problems. When unsure, return "uncertain", not "fail".
Use terminology only for judgment; never replace terms.
"""
)

_DEFAULT_SINGLE_USER = """Source: $original
Translation: $translation
Context: $context
Terminology: $terms

Judge the translation quality. Return only the decision; do not generate or rewrite the translation."""

_DEFAULT_BATCH_SYSTEM = (
    "You are a $game_name localization quality inspector. Determine whether translations from $source_lang meet "
    "$target_lang quality standards.\n\n"
    """Only inspect and judge translations. Never generate or rewrite them.
Evaluate every entry for clear quality problems.

Return only the structured quality decisions and no other text. Preserve each entry ID exactly.

Each verdict must be exactly one of: "pass", "fail", or "uncertain".
- "pass": accurate and complete, with no clear problem
- "fail": a clear error exists and retranslation is recommended
- "uncertain": quality is questionable but the translation is not clearly wrong; human review is required

Always return "fail" for unchanged source echo (except code, identifiers, or numbers) or runaway repetition.

Focus on clear problems. When unsure, return "uncertain", not "fail".
Include every input entry in order. Use terminology only for judgment; never replace terms.
"""
)


def _get_prompts_dir() -> Path:
    """定位 data/prompts/ 目录，兼容开发环境和 PyInstaller 打包环境。"""

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


class QualityGateChecker(BaseChecker):
    """
    质量关卡检测器。

    调用轻量级LLM判断翻译是否存在明显质量问题，输出三态：
    - PASS: 通过，无大问题
    - FAIL: 失败，有明显质量问题（建议打回重翻）
    - UNCERTAIN: 无法判定（需人工审核）

    提示词配置从 data/prompts/quality_gate/default.toml 加载，支持运行时语言注入。
    文件缺失或解析失败时自动 fallback 到内置默认值。
    """

    @property
    def name(self) -> str:
        return "quality_gate"

    def __init__(
        self,
        llm_client: "LLMClient | None" = None,
        term_manager=None,
        batch_size: int = 10,
        game_profile: str = "skyrim_se",
        target_lang: str = "zh_CN",
        max_output_tokens: int | None = None,
    ):
        """
        初始化。

        Args:
            llm_client: LLM客户端，用于质量检测
            term_manager: TermDatabaseManager，用于获取相关术语
            batch_size: 批量检测的条目数（LLM一次判断多条）
            game_profile: 游戏配置文件名（如"skyrim_se"）
            target_lang: 目标语言配置文件名（如"zh_CN"）
        """
        self._llm = llm_client
        self._term_manager = term_manager
        self._batch_size = batch_size
        self._max_output_tokens = validate_max_output_tokens(max_output_tokens)
        self._prompts = self._load_prompts(game_profile, target_lang)

    def _load_prompts(self, game_profile: str, target_lang: str) -> dict:
        """从 TOML 文件加载并校验提示词配置；违规变体回退到内置默认模板。"""
        prompts_dir = _get_prompts_dir()

        # 加载游戏和语言配置（用于变量替换）
        game_data = _load_toml(prompts_dir / "games" / f"{game_profile}.toml")
        language = load_language_profile(target_lang, prompts_dir=prompts_dir)

        # 阶段提示词与目标语言解耦；语言名称通过 ctx 在渲染时注入。
        qg_path = prompts_dir / "quality_gate" / "default.toml"
        qg_data = _load_toml(qg_path)

        # 构建变量上下文
        game = game_data.get("game", {})
        ctx = {
            "game_name": game.get("name", "The Elder Scrolls V: Skyrim Special Edition (SSE)"),
            "source_lang": language.source_language,
            "target_lang": language.target_language,
        }

        single_cfg = qg_data.get("single_check", {})
        batch_cfg = qg_data.get("batch_check", {})

        single_system = _resolve_template(
            name="quality_gate.single.system",
            template=single_cfg.get("system", ""),
            default=_DEFAULT_SINGLE_SYSTEM,
            allowed_variables=_SYSTEM_ALLOWED_VARIABLES,
            required_variables=_SYSTEM_REQUIRED_VARIABLES,
        )
        single_user = _resolve_template(
            name="quality_gate.single.user",
            template=single_cfg.get("user", ""),
            default=_DEFAULT_SINGLE_USER,
            allowed_variables=_SINGLE_USER_ALLOWED_VARIABLES,
            required_variables=_SINGLE_USER_REQUIRED_VARIABLES,
        )
        batch_system = _resolve_template(
            name="quality_gate.batch.system",
            template=batch_cfg.get("system", ""),
            default=_DEFAULT_BATCH_SYSTEM,
            allowed_variables=_SYSTEM_ALLOWED_VARIABLES,
            required_variables=_SYSTEM_REQUIRED_VARIABLES,
        )

        return {
            "ctx": ctx,
            "single_system": single_system.strip(),
            "single_user": single_user.strip(),
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

    def check(self, entry: "TranslationEntry") -> list[PostProcessIssue]:
        """
        检查单个条目的质量。

        如果没有配置LLM，则跳过检测。

        Args:
            entry: 待检查的翻译条目

        Returns:
            发现的质量问题列表
        """
        if not self._llm or not entry.translation:
            return []

        result = self._check_single(entry)
        return self._result_to_issues(entry, result)

    def check_batch(self, entries: list["TranslationEntry"]) -> list[PostProcessIssue]:
        """
        批量检查条目质量。

        使用LLM批量判断以提高效率。

        Args:
            entries: 待检查的条目列表

        Returns:
            发现的质量问题列表
        """
        if not self._llm:
            return []

        issues = []
        # 分批处理
        for i in range(0, len(entries), self._batch_size):
            batch = entries[i : i + self._batch_size]
            batch_issues = self._check_batch_internal(batch)
            issues.extend(batch_issues)

        return issues

    def _check_single(self, entry: "TranslationEntry") -> QualityGateResult:
        """使用LLM检查单个条目。"""
        terms = self._get_relevant_terms(entry)

        # 严格渲染稳定 System 与动态 User
        system_content = self._render_system(self._prompts["single_system"], "quality_gate.single.system")
        user_content = self._render_user(
            self._prompts["single_user"],
            "quality_gate.single.user",
            {
                "original": entry.original or "",
                "translation": entry.translation or "",
                "context": entry.context or "unknown",
                "terms": self._format_terms(terms),
            },
        )
        messages = build_postprocess_messages(
            stage="quality_gate",
            shape="single",
            rendered_system=system_content,
            user_content=user_content,
        )

        try:
            response = self._llm.chat(
                messages=messages,
                max_tokens=output_token_limit(self._max_output_tokens, 500),
            )
            return self._parse_response(response)
        except Exception as e:
            # LLM调用失败，返回uncertain
            return QualityGateResult(
                verdict=QualityVerdict.UNCERTAIN,
                reason=f"检测失败: {e}",
                issues=["质量检测出错，建议人工审核"],
            )

    def _check_batch_internal(self, entries: list["TranslationEntry"]) -> list[PostProcessIssue]:
        """批量检查内部实现。"""
        if not self._llm:
            return []

        # 过滤掉没有译文的条目
        valid_entries = [e for e in entries if e.translation]
        if not valid_entries:
            return []

        # 构建批量检测 User（每条独立术语），并渲染稳定 System
        user_content = self._build_batch_prompt(valid_entries)
        system_content = self._render_system(self._prompts["batch_system"], "quality_gate.batch.system")
        messages = build_postprocess_messages(
            stage="quality_gate",
            shape="batch",
            rendered_system=system_content,
            user_content=user_content,
        )

        try:
            response = self._llm.chat(
                messages=messages,
                max_tokens=output_token_limit(self._max_output_tokens, 2000),
            )
            return self._parse_batch_response(valid_entries, response)
        except Exception as e:
            # 批量检测失败，降级为逐个检测
            issues = []
            for entry in valid_entries:
                result = QualityGateResult(
                    verdict=QualityVerdict.UNCERTAIN,
                    reason=f"批量检测失败: {e}",
                    issues=["质量检测出错，建议人工审核"],
                )
                issues.extend(self._result_to_issues(entry, result))
            return issues

    def _build_batch_prompt(self, entries: list["TranslationEntry"]) -> str:
        """构建批量检测的动态 User 内容。

        每个条目独立携带其为该条原文匹配到的相关术语；不跨条目合并成大术语表。
        无术语时使用与单条一致的"无"语义。
        """
        lines = ["Entries to inspect:"]
        lines.append("-" * 40)

        for entry in entries:
            terms = self._get_relevant_terms(entry)
            lines.append(f"\n[ENTRY_ID: {entry.id}]")
            lines.append(f"Source: {entry.original or ''}")
            lines.append(f"Translation: {entry.translation or ''}")
            lines.append(f"Context: {entry.context or 'unknown'}")
            lines.append(f"Terminology: {self._format_terms(terms)}")

        return "\n".join(lines)

    def _parse_batch_response(self, entries: list["TranslationEntry"], response: str) -> list[PostProcessIssue]:
        """解析批量检测响应。"""
        import json

        try:
            payload = json.loads(response)
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise TypeError("quality gate batch response must contain a results array")
            data = payload["results"]

            issues = []
            entry_map = {alias: entry for entry in entries for alias in {str(entry.id), str(entry.key)}}
            returned_entry_ids: set[str] = set()
            duplicate_entry_ids: set[str] = set()

            for item in data:
                response_id = str(item.get("entry_id", ""))
                entry = entry_map.get(response_id)
                if not entry:
                    continue
                canonical_id = str(entry.id)
                if canonical_id in returned_entry_ids:
                    duplicate_entry_ids.add(canonical_id)
                    continue
                returned_entry_ids.add(canonical_id)

                verdict_str = item.get("verdict", "uncertain").lower()
                if verdict_str == "pass":
                    verdict = QualityVerdict.PASS
                elif verdict_str == "fail":
                    verdict = QualityVerdict.FAIL
                else:
                    verdict = QualityVerdict.UNCERTAIN

                result = QualityGateResult(
                    verdict=verdict,
                    reason=item.get("reason", ""),
                    issues=item.get("issues", []),
                )
                issues.extend(self._result_to_issues(entry, result))

            for entry in entries:
                if str(entry.id) in duplicate_entry_ids:
                    issues.extend(
                        self._result_to_issues(
                            entry,
                            QualityGateResult(
                                verdict=QualityVerdict.UNCERTAIN,
                                reason="批量质量检测响应重复返回该条目",
                                issues=["模型返回重复检测结果，请人工确认质量"],
                            ),
                        )
                    )
                    continue
                if str(entry.id) in returned_entry_ids:
                    continue
                issues.extend(
                    self._result_to_issues(
                        entry,
                        QualityGateResult(
                            verdict=QualityVerdict.UNCERTAIN,
                            reason="批量质量检测响应缺少该条目",
                            issues=["模型未返回检测结果，请人工确认质量"],
                        ),
                    )
                )

            return issues

        except (AttributeError, TypeError, json.JSONDecodeError):
            # 解析失败，降级为逐个检测
            return self._fallback_batch_check(entries, response)

    def _fallback_batch_check(self, entries: list["TranslationEntry"], response: str) -> list[PostProcessIssue]:
        """批量解析失败时的降级处理：尝试文本匹配。"""
        issues = []

        for entry in entries:
            result = QualityGateResult(
                verdict=QualityVerdict.UNCERTAIN,
                reason="批量解析异常，建议人工审核",
                issues=["响应解析失败，请人工确认质量"],
            )
            issues.extend(self._result_to_issues(entry, result))

        return issues

    def _get_relevant_terms(self, entry: "TranslationEntry") -> dict[str, str]:
        """获取与条目相关的术语。"""
        if not self._term_manager or not entry.original:
            return {}

        contextual = getattr(self._term_manager, "match_terms_for_entry", None)
        if callable(contextual):
            return contextual(entry)
        return self._term_manager.match_terms([entry.original])

    def _format_terms(self, terms: dict[str, str]) -> str:
        """格式化术语表供Prompt使用。"""
        if not terms:
            return "none"
        return ", ".join(f"{k}→{v}" for k, v in terms.items())

    def _parse_response(self, response: str) -> QualityGateResult:
        """解析LLM响应。"""
        try:
            # 提取JSON
            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)

            verdict_str = data.get("verdict", "uncertain").lower()
            if verdict_str == "pass":
                verdict = QualityVerdict.PASS
            elif verdict_str == "fail":
                verdict = QualityVerdict.FAIL
            else:
                verdict = QualityVerdict.UNCERTAIN

            return QualityGateResult(
                verdict=verdict,
                reason=data.get("reason", ""),
                issues=data.get("issues", []),
            )
        except (AttributeError, TypeError, json.JSONDecodeError):
            return QualityGateResult(
                verdict=QualityVerdict.UNCERTAIN,
                reason="无法解析LLM响应",
                issues=[response[:200]],
            )

    def _result_to_issues(self, entry: "TranslationEntry", result: QualityGateResult) -> list[PostProcessIssue]:
        """将质量检测结果转换为PostProcessIssue。"""
        if result.verdict == QualityVerdict.PASS:
            return []

        severity = "error" if result.verdict == QualityVerdict.FAIL else "warning"
        suggestion = "建议重新翻译" if result.verdict == QualityVerdict.FAIL else "建议人工审核"

        # 合并issues列表为描述
        details = "; ".join(result.issues) if result.issues else result.reason

        return [
            PostProcessIssue(
                entry_id=entry.id,
                issue_type=PostProcessIssue.LOW_QUALITY,
                severity=severity,
                message=f"[{result.verdict.value}] {result.reason} ({details})",
                original=entry.original or "",
                translation=entry.translation or "",
                suggestion=suggestion,
            )
        ]
