"""
Prompt 构建器与响应解析器。

提示词模板从以下两个 TOML 文件加载，支持多游戏 / 多目标语言扩展：
  data/prompts/games/{game_profile}.toml   — 游戏专属信息（名称、格式标记等）
  data/prompts/langs/{target_lang}.toml    — 目标语言专属模板（翻译风格、提示词）

任一文件缺失或解析失败时，自动 fallback 到内置默认值（简中 + Skyrim SE）。
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

from transbridge.infra.prompt_cache import (
    attach_prompt_cache_directive,
    build_prompt_cache_key,
)

# ── 翻译模式归一化 ─────────────────────────────────────────────────────────────

TranslationMode = Literal["实体短文本", "对话", "长文本"]

_ENTITY_SHORT_MODE = "实体短文本"
_DIALOGUE_MODE = "对话"
_LONG_TEXT_MODE = "长文本"

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

# ── 内置默认值（文件完全缺失时使用，已预先完成变量替换） ─────────────────────

_DEFAULT_TRANSLATION_COMMON_SYSTEM = (
    "你是专业的上古卷轴5：天际特别版（SSE）模组本地化翻译员。\n"
    "翻译要求：\n"
    "1. 措辞自然流畅，符合中文语言习惯。\n"
    "2. 每个输入条目的 terms 仅适用于该条目；提供时必须严格遵循其中的对照翻译。\n"
    "3. 保留原文中的特殊标记，如 <br>、[pagebreak]、\\n 换行符、%s 等格式占位符。\n"
    "4. 不要添加任何解释或注释，只输出 JSON。\n"
    '5. 输出必须是严格的 JSON 对象，格式：{"id1": "译文1", "id2": "译文2", ...}\n'
    "6. 严禁将原文原封不动地作为译文输出。\n"
    "7. 严禁生成重复字符或重复短语的无限循环内容（如连续重复同一个字超过十次）。"
)
_DEFAULT_TRANSLATION_MODE_SYSTEM = "<translation_mode>$translation_mode</translation_mode>"
_DEFAULT_TRANSLATION_USER = (
    "<task_category>$batch_type</task_category>\n"
    "\n"
    "<translation_entries>\n"
    "$input_json\n"
    "</translation_entries>\n"
    "\n"
    "请严格按 SYSTEM 中的 JSON 输出协议返回结果。"
)
_DEFAULT_EXTRACTION_SYSTEM = (
    "你是上古卷轴5：天际特别版（SSE）本地化专家。"
    "请从给定的原文-译文对中提取专有名词（人名、地名、物品名、法术名等），"
    "只提取在原文和译文中都明确出现的配对。\n"
    '输出格式：严格的 JSON 数组，每项为 {"term": "英文原词", "translation": "中文译词"}。\n'
    "不要输出任何解释。"
)
_DEFAULT_EXTRACTION_USER = "请从以下原文-译文对中提取专有名词：\n$pairs_json"


# ── TOML 加载 ────────────────────────────────────────────────────────────────


def _get_prompts_dir() -> Path:
    """定位 data/prompts/ 目录，兼容开发环境和 PyInstaller 打包环境。"""
    from transbridge.paratranz.config_manager import ParatranzConfig

    return Path(ParatranzConfig.get_data_dir()) / "prompts"


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
        lang_data = _load_toml(prompts_dir / "langs" / f"{target_lang}.toml")

        game = game_data.get("game", {})
        lang = lang_data.get("lang", {})
        trans = lang_data.get("translation", {})
        extr = lang_data.get("extraction", {})

        # 变量替换上下文（$var 占位符）
        self._ctx = {
            "game_name": game.get("name", "上古卷轴5：天际特别版（SSE）"),
            "format_notes": game.get(
                "format_notes",
                "保留原文中的特殊标记，如 <br>、[pagebreak]、\\n 换行符、%s 等格式占位符。",
            ),
            "source_lang": lang.get("source", "英文"),
            "target_lang": lang.get("target", "中文"),
        }

        # 兼容旧配置：仅当缺少新键 common_system 时，把旧 translation.system 作为
        # common_system 的迁移输入（旧 system 本就不含动态术语，可直接复用）。
        common_system = trans.get(
            "common_system",
            trans.get("system", _DEFAULT_TRANSLATION_COMMON_SYSTEM),
        ).strip()
        self._translation_common_tpl = common_system
        self._translation_mode_tpl = trans.get("mode_system", _DEFAULT_TRANSLATION_MODE_SYSTEM).strip()
        self._translation_user_tpl = trans.get("user", _DEFAULT_TRANSLATION_USER).strip()
        if "$terms_section" in self._translation_user_tpl:
            warnings.warn(
                "translation.user 中的 $terms_section 已弃用；逐条术语将写入 input_json，该占位符只会渲染为空串"
            )
        self._extraction_system_tpl = extr.get("system", _DEFAULT_EXTRACTION_SYSTEM).strip()
        self._extraction_user_tpl = extr.get("user", _DEFAULT_EXTRACTION_USER).strip()

        # 通用 SYSTEM 一次性渲染 + 稳定 cache key
        self._translation_common_system = self._render(self._translation_common_tpl)
        self._translation_cache_key = build_prompt_cache_key(
            "transbridge.translation.v2",
            self._translation_common_system,
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
            batch_type=batch_type,
            # 仓库外旧模板可能仍保留该占位符；只渲染为空，不恢复批次级术语。
            terms_section="",
            input_json=input_json,
        )

        return [
            common_system_msg,
            mode_system_msg,
            {"role": "user", "content": user_content},
        ]

    def build_extraction_prompt(self, translated_pairs: list[dict]) -> list[dict]:
        pairs_json = json.dumps(translated_pairs, ensure_ascii=False, indent=2)
        return [
            {"role": "system", "content": self._render(self._extraction_system_tpl)},
            {"role": "user", "content": self._render(self._extraction_user_tpl, pairs_json=pairs_json)},
        ]

    def parse_translation_response(self, response: str, expected_ids: set[str]) -> dict[str, str]:
        """解析 LLM 返回的 JSON，返回 {id: translation}，容错处理。
        当 JSON 因上下文溢出被截断时，退化到逐对提取完整键值对，避免丢弃已翻译部分。
        """
        text = response.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
        elif start != -1:
            text = text[start:]  # 截断响应：有 { 但无 }
        try:
            data = json.loads(text)
        except Exception:
            # JSON 不完整（典型原因：输出 token 耗尽导致截断）
            # 用正则逐对提取已完成的键值对，只重试真正缺失的条目
            data = _extract_partial_json_pairs(text)
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if k in expected_ids and v}

    def extract_partial_pairs(self, buffer: str) -> dict[str, str]:
        """从不完整的流式 buffer 中提取已完成的翻译对，供增量处理使用。"""
        return _extract_partial_json_pairs(buffer)

    def parse_extraction_response(self, response: str) -> list[dict]:
        """解析专有名词抽取结果，返回 [{"term": ..., "translation": ...}]。"""
        text = response.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start : end + 1]
        try:
            data = json.loads(text)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and "term" in item and "translation" in item]

    def parse_hybrid_response(self, response: str) -> dict:
        """解析 LLM 混合模式响应（ReAct + Plan 双模式）。

        Returns: {"mode": "plan"|"react", "thought": str, "steps": [{"id", "tool", "args", "depends_on"}]}
        """
        text = response.strip()
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
        raw = json_match.group(1) if json_match else text

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = self._try_fix_truncated_json(raw)
            if data is None:
                return {"mode": "react", "thought": response, "steps": []}

        if not isinstance(data, dict):
            return {"mode": "react", "thought": response, "steps": []}

        mode = data.get("mode", "react")
        thought = data.get("thought", "")
        steps = data.get("steps") or []
        tool_calls = data.get("tool_calls") or []

        if tool_calls and not steps:
            steps = [
                {
                    "id": i + 1,
                    "tool": tc.get("tool", ""),
                    "args": tc.get("args", {}),
                    "depends_on": tc.get("depends_on", []),
                }
                for i, tc in enumerate(tool_calls)
            ]

        return {"mode": mode, "thought": thought, "steps": steps}

    def _try_fix_truncated_json(self, text: str) -> dict | None:
        """尝试修复因 max_tokens 截断的 JSON。"""
        text = text.strip()
        if not text.startswith("{"):
            return None
        for suffix in ("}", '"]}', '"}', "}]}"):
            candidate = text.rstrip(",").rstrip() + suffix
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None
