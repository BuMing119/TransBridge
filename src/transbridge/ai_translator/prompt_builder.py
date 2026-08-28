"""
Prompt 构建器与响应解析器。

提示词模板与语言档案分别从以下 TOML 文件加载：
  data/prompts/games/{game_profile}.toml   — 游戏专属信息（名称、格式标记等）
  data/prompts/langs/{target_lang}.toml    — 语言名称与可选示例
  data/prompts/translation/default.toml    — 通用翻译提示词
  data/prompts/extraction/default.toml     — 通用术语抽取提示词

提示词文件缺失或解析失败时使用通用内置模板；语言档案缺失或无效时直接报错。
模板使用 $var 占位符（string.Template），不与 JSON 示例中的花括号冲突。
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from string import Template
import tomllib
from typing import TYPE_CHECKING, Literal
import warnings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from transbridge.converter.translation_entry import TranslationEntry

from transbridge.config.language_profiles import LanguageProfile, load_language_profile
from transbridge.config.paths import get_data_resource_dir
from transbridge.infra.llm_structured_outputs import attach_structured_output_directive
from transbridge.infra.prompt_cache import (
    attach_prompt_cache_directive,
    build_prompt_cache_key,
)

from .structured_schemas import TERM_EXTRACTION_OUTPUT_SCHEMA, TRANSLATION_OUTPUT_SCHEMA

# ── 翻译模式归一化 ─────────────────────────────────────────────────────────────

TranslationMode = Literal["entity_short_text", "dialogue", "long_text"]

_ENTITY_SHORT_MODE = "entity_short_text"
_DIALOGUE_MODE = "dialogue"
_LONG_TEXT_MODE = "long_text"

# 具体分类 → 实体短文本
_ENTITY_SHORT_BATCH_TYPES = frozenset({
    "种族与派系",
    "人名",
    "地名",
    "书名",
    "物品",
    "法术技能",
    "任务名",
    "互动",
})
# 具体分类 → 对话
_DIALOGUE_BATCH_TYPES = frozenset({"对话"})
_BATCH_TYPE_PROMPT_LABELS = {
    "种族与派系": "races and factions",
    "人名": "character names",
    "地名": "locations",
    "书名": "book titles",
    "物品": "items",
    "法术技能": "spells and skills",
    "任务名": "quest titles",
    "互动": "interactions",
    "对话": "dialogue",
    "长文本": "long text",
    "书籍内容": "book content",
    "任务日志": "quest journal",
    "其他": "other",
}

# ── 内置默认值（文件完全缺失时使用，已预先完成变量替换） ─────────────────────

_DEFAULT_TRANSLATION_COMMON_SYSTEM = (
    "You are a professional mod localization translator for $game_name.\n"
    "Translate from $source_lang into $target_lang.\n"
    "Translation requirements:\n"
    "1. Produce natural, fluent translations that follow $target_lang conventions.\n"
    "2. Each entry's terms apply only to that entry; follow every provided mapping exactly.\n"
    "3. Preserve source markers such as <br>, [pagebreak], \\n, and format placeholders such as %s.\n"
    "4. Return only the structured translation result, with no explanations or comments.\n"
    "5. Return exactly one translation for every input entry and preserve each entry ID exactly.\n"
    "6. Never omit, duplicate, or invent entries.\n"
    "7. Never echo the source text unchanged as the translation.\n"
    "8. Never generate runaway repeated characters or phrases.\n"
    "$fixed_examples"
)
_DEFAULT_TRANSLATION_MODE_SYSTEM = "<translation_mode>$translation_mode</translation_mode>"
_DEFAULT_TRANSLATION_USER = (
    "<task_category>$batch_type</task_category>\n"
    "\n"
    "<translation_entries>\n"
    "$input_json\n"
    "</translation_entries>\n"
    "\n"
    "Return one translation for every input entry and preserve each entry ID exactly."
)
_DEFAULT_EXTRACTION_SYSTEM = (
    "You are a localization expert for $game_name working from $source_lang into $target_lang. "
    "Extract proper nouns from the provided source-translation pairs. Include only substrings that appear "
    "continuously and verbatim in both texts of the same pair; do not alter case, inflection, or punctuation.\n"
    "Return only the structured extraction result. Include each extracted source term with its translated "
    "counterpart, and do not output any explanation."
)
_DEFAULT_EXTRACTION_USER = "Extract proper nouns from the following source-translation pairs:\n$pairs_json"


# ── TOML 加载 ────────────────────────────────────────────────────────────────


def _get_prompts_dir() -> Path:
    """定位 data/prompts/ 目录，兼容开发环境和 PyInstaller 打包环境。"""

    return Path(get_data_resource_dir("prompts"))


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        warnings.warn(f"加载 {path.name} 失败，使用内置默认提示词：{e}")
        return {}


# ── 截断 JSON 容错提取 ────────────────────────────────────────────────────────


def _extract_partial_json_pairs(text: str) -> dict:
    """从被截断的 JSON 对象中提取已完整输出的字符串键值对。

    处理场景：LLM 因 max_tokens 耗尽，在 JSON 对象中途截断。
    只匹配 key 和 value 均为完整 JSON 字符串的对，跳过末尾残缺部分。
    """
    result = {}
    # 匹配完整的 "key": "value" 对，支持 JSON 转义序列（\n \t \" \\ 等）
    pattern = re.compile(r'"((?:[^"\\]|\\.)*?)"\s*:\s*"((?:[^"\\]|\\.)*?)"')
    for m in pattern.finditer(text):
        try:
            key = json.loads(f'"{m.group(1)}"')
            value = json.loads(f'"{m.group(2)}"')
        except Exception:
            continue
        if key and value:
            result[key] = value
    return result


def _extract_partial_translation_results(text: str) -> dict[str, str]:
    """Extract complete translation items from a possibly incomplete results array."""

    results_key = re.search(r'"results"\s*:\s*\[', text)
    if results_key is None:
        return {}
    decoder = json.JSONDecoder()
    index = results_key.end()
    extracted: dict[str, str] = {}
    duplicates: set[str] = set()
    while index < len(text):
        while index < len(text) and text[index] in " \t\r\n,":
            index += 1
        if index >= len(text) or text[index] == "]":
            break
        try:
            item, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        index = end
        if not isinstance(item, dict) or set(item) != {"entry_id", "translation"}:
            continue
        entry_id = item.get("entry_id")
        translation = item.get("translation")
        if not isinstance(entry_id, str) or not isinstance(translation, str) or not translation:
            continue
        if entry_id in extracted:
            duplicates.add(entry_id)
        else:
            extracted[entry_id] = translation
    for entry_id in duplicates:
        extracted.pop(entry_id, None)
    return extracted


# ── PromptBuilder ────────────────────────────────────────────────────────────


class PromptBuilder:
    """
    根据游戏配置和目标语言配置构建翻译/抽取提示词。

    Args:
        game_profile: 游戏配置文件名（不含后缀），对应 data/prompts/games/{name}.toml
        target_lang:  目标语言配置文件名（不含后缀），对应 data/prompts/langs/{name}.toml
    """

    def __init__(self, game_profile: str = "skyrim_se", target_lang: str = "zh_CN") -> None:
        prompts_dir = _get_prompts_dir()
        game_data = _load_toml(prompts_dir / "games" / f"{game_profile}.toml")
        language = load_language_profile(target_lang, prompts_dir=prompts_dir)
        translation_data = _load_toml(prompts_dir / "translation" / "default.toml")
        extraction_data = _load_toml(prompts_dir / "extraction" / "default.toml")

        game = game_data.get("game", {})
        trans = translation_data.get("translation", {})
        extr = extraction_data.get("extraction", {})

        # 变量替换上下文（$var 占位符）
        self._ctx = {
            "game_name": game.get("name", "The Elder Scrolls V: Skyrim Special Edition (SSE)"),
            "format_notes": game.get(
                "format_notes",
                "Preserve source markers such as <br>, [pagebreak], \\n, and format placeholders such as %s.",
            ),
            "source_lang": language.source_language,
            "target_lang": language.target_language,
            "fixed_examples": self._format_fixed_examples(language),
        }

        common_system = trans.get("common_system", _DEFAULT_TRANSLATION_COMMON_SYSTEM).strip()
        self._translation_common_tpl = common_system
        self._translation_mode_tpl = trans.get("mode_system", _DEFAULT_TRANSLATION_MODE_SYSTEM).strip()
        self._translation_user_tpl = trans.get("user", _DEFAULT_TRANSLATION_USER).strip()
        self._extraction_system_tpl = extr.get("system", _DEFAULT_EXTRACTION_SYSTEM).strip()
        self._extraction_user_tpl = extr.get("user", _DEFAULT_EXTRACTION_USER).strip()

        # 通用 SYSTEM 一次性渲染 + 稳定 cache key
        self._translation_common_system = self._render(self._translation_common_tpl)
        self._translation_cache_key = build_prompt_cache_key(
            "transbridge.translation.v2",
            self._translation_common_system,
        )

    @staticmethod
    def _format_fixed_examples(language: LanguageProfile) -> str:
        if language.example_source is None or language.example_target is None:
            return ""
        return (
            "<fixed_examples>\n"
            f"Source: {language.example_source}\n"
            f"Translation: {language.example_target}\n"
            "</fixed_examples>"
        )

    def _render(self, template: str, **extra) -> str:
        """将 $var 占位符替换为实际值，未知占位符保留原样。"""
        return Template(template).safe_substitute({**self._ctx, **extra})

    def _translation_mode(self, batch_type: str) -> TranslationMode:
        """把具体分类归一化为三种稳定模式之一。未知分类回退到“长文本”。"""
        if batch_type in _ENTITY_SHORT_BATCH_TYPES:
            return _ENTITY_SHORT_MODE
        if batch_type in _DIALOGUE_BATCH_TYPES:
            return _DIALOGUE_MODE
        return _LONG_TEXT_MODE

    def _build_mode_system(self, translation_mode: str) -> str:
        """渲染模式 SYSTEM，只声明三种模式标签之一。"""
        return self._render(self._translation_mode_tpl, translation_mode=translation_mode)

    @staticmethod
    def _prompt_batch_type(batch_type: str) -> str:
        """Render internal Chinese categories as English without changing shared UI labels."""
        return _BATCH_TYPE_PROMPT_LABELS.get(batch_type, batch_type)

    @staticmethod
    def _scope_flat_terms(
        entries: list[TranslationEntry],
        matched_terms: Mapping[str, str],
    ) -> dict[str, dict[str, str]]:
        """兼容旧调用：只能恢复可在原文中直接定位的平面术语。"""
        return {
            entry.key: {
                term: translation
                for term, translation in matched_terms.items()
                if term.lower() in entry.original.lower()
            }
            for entry in entries
        }

    def build_translation_prompt(
        self,
        entries: list[TranslationEntry],
        matched_terms: Mapping[str, str],
        batch_type: str,
        *,
        terms_by_entry: Mapping[str, Mapping[str, str]] | None = None,
    ) -> list[dict]:
        translation_mode = self._translation_mode(batch_type)

        common_system_msg = attach_prompt_cache_directive(
            {"role": "system", "content": self._translation_common_system},
            cache_key=self._translation_cache_key,
            profile="translation_layered",
            breakpoint="A",
        )
        mode_system_msg = attach_prompt_cache_directive(
            {"role": "system", "content": self._build_mode_system(translation_mode)},
            cache_key=self._translation_cache_key,
            profile="translation_layered",
            breakpoint="B",
        )

        scoped_terms = terms_by_entry if terms_by_entry is not None else self._scope_flat_terms(entries, matched_terms)
        input_payload: dict[str, dict[str, object]] = {}
        for entry in entries:
            item: dict[str, object] = {"source": entry.original}
            entry_terms = scoped_terms.get(entry.key, {})
            if entry_terms:
                item["terms"] = dict(entry_terms)
            input_payload[entry.key] = item
        input_json = json.dumps(input_payload, ensure_ascii=False, indent=2)
        user_content = self._render(
            self._translation_user_tpl,
            batch_type=self._prompt_batch_type(batch_type),
            input_json=input_json,
        )

        user_msg = attach_structured_output_directive(
            {"role": "user", "content": user_content},
            TRANSLATION_OUTPUT_SCHEMA,
        )
        return [
            common_system_msg,
            mode_system_msg,
            user_msg,
        ]

    def build_extraction_prompt(self, translated_pairs: list[dict]) -> list[dict]:
        pairs_json = json.dumps(translated_pairs, ensure_ascii=False, indent=2)
        user_msg = attach_structured_output_directive(
            {"role": "user", "content": self._render(self._extraction_user_tpl, pairs_json=pairs_json)},
            TERM_EXTRACTION_OUTPUT_SCHEMA,
        )
        return [
            {"role": "system", "content": self._render(self._extraction_system_tpl)},
            user_msg,
        ]

    def parse_translation_response(self, response: str, expected_ids: set[str]) -> dict[str, str]:
        """Parse the native results envelope and keep only requested, unique entries."""
        try:
            data = json.loads(response)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return {}
        parsed: dict[str, str] = {}
        duplicates: set[str] = set()
        for item in data["results"]:
            if not isinstance(item, dict):
                continue
            entry_id = item.get("entry_id")
            translation = item.get("translation")
            if entry_id not in expected_ids or not isinstance(translation, str) or not translation:
                continue
            if entry_id in parsed:
                duplicates.add(entry_id)
            else:
                parsed[entry_id] = translation
        for entry_id in duplicates:
            parsed.pop(entry_id, None)
        return parsed

    def extract_partial_pairs(self, buffer: str) -> dict[str, str]:
        """从不完整的流式 buffer 中提取已完成的翻译对，供增量处理使用。"""
        return _extract_partial_translation_results(buffer)

    def parse_extraction_response(self, response: str) -> list[dict]:
        """解析专有名词抽取结果，返回 [{"term": ..., "translation": ...}]。"""
        text = response.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
        try:
            data = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            return []
        return [item for item in data["results"] if isinstance(item, dict) and "term" in item and "translation" in item]
