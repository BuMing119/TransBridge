"""
LLM润色器。

对译文进行风格优化和流畅度提升，无需前置问题检测。
"""

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import tomllib
from typing import TYPE_CHECKING, Any
import warnings

from transbridge.config.language_profiles import load_language_profile

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


def _normalize_polish_changes(value: object) -> list[dict[str, Any]]:
    """Coerce provider variations into the mapping shape used downstream.

    Some compatible providers return ``changes`` as ``["style", "fluency"]``
    even though the prompt requests objects.  Keeping those raw strings lets a
    single item crash an entire arbitration batch when it calls ``change.get``.
    Preserve the useful label as ``aspect`` while normalizing the contract at
    the result boundary.
    """
    if value is None:
        return []
    if isinstance(value, dict):
        items = [value]
    elif isinstance(value, str):
        items = [value]
    else:
        try:
            items = list(value)
        except TypeError:
            return []

    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(dict(item))
        elif isinstance(item, str) and (label := item.strip()):
            normalized.append({"aspect": label, "before": "", "after": "", "reason": ""})
    return normalized


@dataclass
class PolishResult:
    """润色结果。"""

    entry_id: str
    original_translation: str
    polished_translation: str
    changes: list[dict] = field(default_factory=list)  # 改动说明
    confidence: float = 0.0  # 润色信心度
    needs_arbitration: bool = False  # 是否需要裁决
    note: str = ""  # 额外说明

    def __post_init__(self) -> None:
        self.changes = _normalize_polish_changes(self.changes)


# ── 内置默认提示词 ─────────────────────────────────────────────────────────

_DEFAULT_SYSTEM = (
    "You are a $game_name localization polishing specialist. Polish translations of $source_lang source text into "
    "more natural $target_lang that fits the game context.\n\n"
    """Improve fluency, style, and contextual fit without retranslating or introducing new meaning.

Polishing rules:
1. Improve fluency, word order, and naturalness; avoid redundancy and repetition.
2. Match the game's genre, tone, and character voices.
3. Consider context, preserve terminology, emotion, and tone.
4. Preserve every source placeholder and formatting marker, and do not change meaning.
   Close paired punctuation correctly and use provided terminology.

Polishing levels (the active level is provided by User):
- light: correct only clear problems and preserve the existing style
- moderate: moderately improve fluency and expression
- aggressive: polish deeply for the strongest expression

Return only the structured polishing result. Classify every reported change as fluency, style, context, or terminology.
Confidence ranges from 0 to 1. Request arbitration when substantial changes require confirmation.
If the existing translation is already strong, changes may be empty and confidence should be high.
"""
)

_DEFAULT_USER = """[SOURCE]
$original

[CURRENT TRANSLATION]
$current_translation

[CONTEXT]
$context

[RELEVANT TERMINOLOGY]
$terms

[POLISHING SETTINGS]
Level: $polish_level

Polish the current translation and return only the polishing result."""

_DEFAULT_BATCH_SYSTEM = (
    "You are a $game_name localization polishing specialist. Polish multiple translations of $source_lang source "
    "text into more natural $target_lang that fits the game context.\n\n"
    """Improve fluency and style without retranslating.

Preserve every placeholder and formatting marker, use standard terminology, and do not change source meaning.

Polishing levels (the active level is provided by User): light, moderate, or aggressive.

Return only the structured polishing results, with one result for every input entry. Preserve each entry ID exactly.
Classify every reported change as fluency, style, context, or terminology.
Confidence above 0.9 means the result is strong.
If the existing translation is already strong, changes may be empty."""
)


def _get_prompts_dir() -> Path:
    """定位 data/prompts/ 目录。"""
    from ...paratranz.config_manager import ParatranzConfig

    return Path(ParatranzConfig.get_data_dir()) / "prompts"


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
# 单条 User 模板的 required 动态变量
_POLISH_USER_REQUIRED = frozenset({"original", "current_translation", "context", "terms", "polish_level"})


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


class LLMPolisher:
    """
    LLM润色器。

    职责：对译文进行风格优化和流畅度提升，无需前置问题检测。
    与 LLMRefiner 的区别：Refiner 修复错误，Polisher 提升质量。
    """

    def __init__(
        self,
        llm_client: "LLMClient",
        term_manager: "TermDatabaseManager | None" = None,
        game_profile: str = "skyrim_se",
        target_lang: str = "zh_CN",
        polish_level: str = "moderate",  # light/moderate/aggressive
        max_output_tokens: int | None = None,
    ):
        """
        初始化润色器。

        Args:
            llm_client: LLM客户端
            term_manager: 术语管理器（用于获取相关术语）
            game_profile: 游戏配置文件名
            target_lang: 目标语言配置文件名
            polish_level: 润色强度
        """
        self._llm = llm_client
        self._term_manager = term_manager
        self._polish_level = polish_level
        self._max_output_tokens = validate_max_output_tokens(max_output_tokens)
        self._prompts = self._load_prompts(game_profile, target_lang)

    def _load_prompts(self, game_profile: str, target_lang: str) -> dict:
        """从 TOML 文件加载提示词配置。"""
        prompts_dir = _get_prompts_dir()

        # 加载游戏和语言配置
        game_data = _load_toml(prompts_dir / "games" / f"{game_profile}.toml")
        language = load_language_profile(target_lang, prompts_dir=prompts_dir)

        # 阶段提示词与目标语言解耦；语言名称通过 ctx 在渲染时注入。
        polish_path = prompts_dir / "polish" / "default.toml"
        polish_data = _load_toml(polish_path)

        # 构建变量上下文
        game = game_data.get("game", {})
        ctx = {
            "game_name": game.get("name", "The Elder Scrolls V: Skyrim Special Edition (SSE)"),
            "source_lang": language.source_language,
            "target_lang": language.target_language,
        }

        polish_cfg = polish_data.get("polish", {})

        # System 只允许稳定变量；三种润色级别定义稳定在 System。
        system_tpl = _resolve_system_template(
            "polish.system",
            polish_cfg.get("system", _DEFAULT_SYSTEM),
            _DEFAULT_SYSTEM,
            ctx,
        )
        batch_system_tpl = _resolve_system_template(
            "polish.batch_system",
            polish_cfg.get("batch_system", _DEFAULT_BATCH_SYSTEM),
            _DEFAULT_BATCH_SYSTEM,
            ctx,
        )
        # 单条 User 模板需包含全部动态字段。
        user_tpl = _resolve_user_template(
            "polish.user",
            polish_cfg.get("user", _DEFAULT_USER),
            _DEFAULT_USER,
            _POLISH_USER_REQUIRED,
        )

        return {
            "ctx": ctx,
            "system": system_tpl,
            "system_rendered": _render_system("polish.system", system_tpl, ctx),
            "batch_system": batch_system_tpl,
            "batch_system_rendered": _render_system("polish.batch_system", batch_system_tpl, ctx),
            "user": user_tpl,
        }

    def _render_user(self, **extra) -> str:
        """严格渲染单条 User 模板；失败抛 PromptTemplateContractError。"""
        return render_prompt_template(
            name="polish.user",
            template=self._prompts["user"],
            values={**self._prompts["ctx"], **extra},
        )

    def polish(self, entry: "TranslationEntry") -> PolishResult:
        """
        对单个条目进行润色。

        Args:
            entry: 待润色的翻译条目

        Returns:
            润色结果
        """
        # 构建Prompt
        messages = self._build_polish_prompt(entry)

        try:
            response = self._llm.chat(
                messages=messages,
                max_tokens=output_token_limit(self._max_output_tokens, 2000),
            )
            return self._parse_polish_response(entry, response)
        except Exception as e:
            # LLM调用失败，返回原始译文
            return PolishResult(
                entry_id=entry.id,
                original_translation=entry.translation or "",
                polished_translation=entry.translation or "",
                confidence=0.0,
                needs_arbitration=True,
                note=f"LLM润色失败: {e}",
            )

    def polish_batch(self, entries: list["TranslationEntry"]) -> dict[str, PolishResult]:
        """
        批量润色条目。

        Args:
            entries: 待润色的条目列表

        Returns:
            entry_id -> 润色结果的映射
        """
        if not entries:
            return {}

        # 构建批量Prompt
        messages = self._build_batch_polish_prompt(entries)

        try:
            response = self._llm.chat(
                messages=messages,
                max_tokens=output_token_limit(self._max_output_tokens, 4000),
            )
            return self._parse_batch_polish_response(entries, response)
        except Exception as e:
            # 批量失败，降级为逐个处理
            results = {}
            for entry in entries:
                results[entry.id] = PolishResult(
                    entry_id=entry.id,
                    original_translation=entry.translation or "",
                    polished_translation=entry.translation or "",
                    confidence=0.0,
                    needs_arbitration=True,
                    note=f"批量润色失败: {e}",
                )
            return results

    def _build_polish_prompt(self, entry: "TranslationEntry") -> list[dict]:
        """构建润色Prompt（SYSTEM(FINAL) -> USER）。"""
        # 获取相关术语
        terms_text = self._get_relevant_terms_text(entry)

        # 确定润色级别描述
        polish_level_desc = self._get_polish_level_desc()

        # 严格渲染用户Prompt（当前选中级别放动态 User，不改变 System/cache key）
        user_content = self._render_user(
            original=entry.original or "",
            current_translation=entry.translation or "",
            context=entry.context or "unknown",
            terms=terms_text,
            polish_level=polish_level_desc,
        )

        return build_postprocess_messages(
            stage="polish",
            shape="single",
            rendered_system=self._prompts["system_rendered"],
            user_content=user_content,
        )

    def _build_batch_polish_prompt(self, entries: list["TranslationEntry"]) -> list[dict]:
        """构建批量润色Prompt（SYSTEM(FINAL) -> USER）。"""
        lines = ["Polish the following translation entries.\n"]
        lines.append(f"Polishing level: {self._get_polish_level_desc()}\n")

        for entry in entries:
            lines.append(f"\n{'=' * 60}")
            lines.append(f"【ENTRY_ID: {entry.id}】")
            lines.append(f"Source: {entry.original or ''}")
            lines.append(f"Current translation: {entry.translation or ''}")
            lines.append(f"Context: {entry.context or 'unknown'}")

            # 获取该条目的相关术语
            terms = self._get_relevant_terms(entry)
            if terms:
                lines.append("Relevant terminology:")
                for term, trans in terms.items():
                    lines.append(f"  {term} → {trans}")

        lines.append(f"\n{'=' * 60}")

        return build_postprocess_messages(
            stage="polish",
            shape="batch",
            rendered_system=self._prompts["batch_system_rendered"],
            user_content="\n".join(lines),
        )

    def _get_relevant_terms(self, entry: "TranslationEntry") -> dict[str, str]:
        """获取与条目相关的术语。"""
        if not self._term_manager or not entry.original:
            return {}

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

    def _get_polish_level_desc(self) -> str:
        """获取润色级别描述。"""
        desc_map = {
            "light": "correct only clear problems and preserve the existing style",
            "moderate": "moderately improve fluency and expression",
            "aggressive": "polish deeply for the strongest expression",
        }
        return desc_map.get(self._polish_level, "moderate polishing")

    def _parse_polish_response(
        self,
        entry: "TranslationEntry",
        response: str,
    ) -> PolishResult:
        """解析润色响应。"""
        try:
            # 提取JSON
            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(response)

            return PolishResult(
                entry_id=entry.id,
                original_translation=entry.translation or "",
                polished_translation=data.get("polished_translation", entry.translation or ""),
                changes=data.get("changes", []),
                confidence=data.get("confidence", 0.0),
                needs_arbitration=data.get("needs_arbitration", False),
                note=data.get("note", ""),
            )

        except json.JSONDecodeError:
            # JSON解析失败，返回原始译文
            return PolishResult(
                entry_id=entry.id,
                original_translation=entry.translation or "",
                polished_translation=entry.translation or "",
                confidence=0.0,
                needs_arbitration=True,
                note=f"响应解析失败: {response[:200]}",
            )

    def _parse_batch_polish_response(
        self,
        entries: list["TranslationEntry"],
        response: str,
    ) -> dict[str, PolishResult]:
        """解析批量润色响应。"""
        entry_map = {alias: entry for entry in entries for alias in {str(entry.id), str(entry.key)}}
        results = {}
        duplicate_entry_ids: set[str] = set()

        try:
            payload = json.loads(response)
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise TypeError("polish batch response must contain a results array")
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

                results[entry_id] = PolishResult(
                    entry_id=entry_id,
                    original_translation=entry.translation or "",
                    polished_translation=item.get("polished_translation", entry.translation or ""),
                    changes=item.get("changes", []),
                    confidence=item.get("confidence", 0.0),
                    needs_arbitration=item.get("needs_arbitration", False),
                    note=item.get("note", ""),
                )

            for entry in entries:
                if entry.id in duplicate_entry_ids:
                    results[entry.id] = PolishResult(
                        entry_id=entry.id,
                        original_translation=entry.translation or "",
                        polished_translation=entry.translation or "",
                        confidence=0.0,
                        needs_arbitration=True,
                        note="批量润色响应重复返回该条目",
                    )
                    continue
                if entry.id not in results:
                    results[entry.id] = PolishResult(
                        entry_id=entry.id,
                        original_translation=entry.translation or "",
                        polished_translation=entry.translation or "",
                        confidence=0.0,
                        needs_arbitration=True,
                        note="批量润色响应缺少该条目",
                    )

        except (AttributeError, TypeError, json.JSONDecodeError):
            # JSON解析失败，所有条目标记为失败
            for entry in entries:
                results[entry.id] = PolishResult(
                    entry_id=entry.id,
                    original_translation=entry.translation or "",
                    polished_translation=entry.translation or "",
                    confidence=0.0,
                    needs_arbitration=True,
                    note=f"批量响应解析失败: {response[:200]}",
                )

        return results
