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
import re
import tomllib
import warnings
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.transbridge.converter.translation_entry import TranslationEntry

# ── 内置默认值（文件完全缺失时使用，已预先完成变量替换） ─────────────────────

_DEFAULT_TRANSLATION_SYSTEM = (
    "你是专业的上古卷轴5：天际特别版（SSE）模组本地化翻译员。\n"
    "翻译要求：\n"
    "1. 措辞自然流畅，符合中文语言习惯。\n"
    "2. 人名、地名、专有名词请严格遵循术语表中的对照翻译。\n"
    "3. 保留原文中的特殊标记，如 <br>、[pagebreak]、\\n 换行符、%s 等格式占位符。\n"
    "4. 不要添加任何解释或注释，只输出 JSON。\n"
    '5. 输出必须是严格的 JSON 对象，格式：{"id1": "译文1", "id2": "译文2", ...}'
)
_DEFAULT_TRANSLATION_USER = (
    "请将以下【$batch_type】类型的词条从英文翻译成中文。\n"
    "输入 JSON：\n"
    "$input_json\n\n"
    "请直接输出翻译结果 JSON，不要添加任何其他文字。"
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
    from src.transbridge.paratranz.config_manager import ParatranzConfig
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
            "game_name":    game.get("name", "上古卷轴5：天际特别版（SSE）"),
            "format_notes": game.get("format_notes", "保留原文中的特殊标记，如 <br>、[pagebreak]、\\n 换行符、%s 等格式占位符。"),
            "source_lang":  lang.get("source", "英文"),
            "target_lang":  lang.get("target", "中文"),
        }

        self._translation_system_tpl = trans.get("system", _DEFAULT_TRANSLATION_SYSTEM).strip()
        self._translation_user_tpl   = trans.get("user",   _DEFAULT_TRANSLATION_USER).strip()
        self._extraction_system_tpl  = extr.get("system",  _DEFAULT_EXTRACTION_SYSTEM).strip()
        self._extraction_user_tpl    = extr.get("user",    _DEFAULT_EXTRACTION_USER).strip()

    def _render(self, template: str, **extra) -> str:
        """将 $var 占位符替换为实际值，未知占位符保留原样。"""
        return Template(template).safe_substitute({**self._ctx, **extra})

    def build_translation_prompt(
        self,
        entries: list["TranslationEntry"],
        matched_terms: dict[str, str],
        batch_type: str,
    ) -> list[dict]:
        system = self._render(self._translation_system_tpl)
        if matched_terms:
            terms_lines = "\n".join(f"  {t} → {tr}" for t, tr in matched_terms.items())
            system += f"\n\n【术语表（必须遵循）】\n{terms_lines}"

        input_json = json.dumps({e.id: e.original for e in entries}, ensure_ascii=False, indent=2)
        user_content = self._render(self._translation_user_tpl, batch_type=batch_type, input_json=input_json)

        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_content},
        ]

    def build_extraction_prompt(self, translated_pairs: list[dict]) -> list[dict]:
        pairs_json = json.dumps(translated_pairs, ensure_ascii=False, indent=2)
        return [
            {"role": "system", "content": self._render(self._extraction_system_tpl)},
            {"role": "user",   "content": self._render(self._extraction_user_tpl, pairs_json=pairs_json)},
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
            text = text[start:end + 1]
        elif start != -1:
            text = text[start:]   # 截断响应：有 { 但无 }
        try:
            data = json.loads(text)
        except Exception:
            # JSON 不完整（典型原因：输出 token 耗尽导致截断）
            # 用正则逐对提取已完成的键值对，只重试真正缺失的条目
            data = _extract_partial_json_pairs(text)
        if not isinstance(data, dict):
            return {}
        return {k: str(v) for k, v in data.items() if k in expected_ids and v}

    def parse_extraction_response(self, response: str) -> list[dict]:
        """解析专有名词抽取结果，返回 [{"term": ..., "translation": ...}]。"""
        text = response.strip()
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if match:
            text = match.group(1).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start:end + 1]
        try:
            data = json.loads(text)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and "term" in item and "translation" in item]
