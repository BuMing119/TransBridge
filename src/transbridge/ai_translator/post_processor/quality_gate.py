"""
质量关卡检测器：调用LLM判断翻译是否存在明显质量问题。

检测逻辑（由LLM判断）：
- 有大问题 → 标记为失败（建议打回重翻）
- 无大问题 → 通过
- 无法判定 → 标记为待定（需人工审核）

提示词配置从 data/prompts/quality_gate/{target_lang}.toml 加载，支持多游戏/多目标语言扩展。
"""

import json
import re
import tomllib
import warnings
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from .base import BaseChecker, PostProcessIssue

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry
    from ..llm_client import LLMClient


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


# ── 内置默认值（文件缺失时使用）────────────────────────────────────────────

_DEFAULT_SINGLE_SYSTEM = """你是游戏本地化质量检测员。请判断以下翻译是否存在明显质量问题。

必须严格按JSON格式输出，不要添加任何其他文字：
{
    "verdict": "pass" | "fail" | "uncertain",
    "reason": "判定理由，简洁说明",
    "issues": ["具体问题1", "具体问题2", ...]
}

判定标准：
- "pass": 翻译准确、完整，无明显问题
- "fail": 有明显错误（漏翻、错翻、格式损坏、术语错误、回显原文、重复输出循环等），建议打回重翻
- "uncertain": 质量存疑但不确定是否错误，需人工审核

特别注意以下必须判为 "fail" 的情况：
- 译文直接将原文原封不动复制输出（回显原文），除非原文明显是不需要翻译的代码/标识符/数字
- 译文存在同一字符或短语的无限重复输出（如连续重复同一个字超过十次）

注意：
- 不要吹毛求疵，只关注明显问题
- 如果拿不准，返回"uncertain"而非"fail"
"""

_DEFAULT_SINGLE_USER = """原文：{original}
译文：{translation}
上下文：{context}
术语表：{terms}

请判断译文质量，按要求的JSON格式输出。"""

_DEFAULT_BATCH_SYSTEM = """你是游戏本地化质量检测员。请批量判断以下翻译是否存在明显质量问题。

必须严格按JSON数组格式输出，不要添加任何其他文字：
[
    {
        "entry_id": "条目ID",
        "verdict": "pass" | "fail" | "uncertain",
        "reason": "判定理由，简洁说明",
        "issues": ["具体问题1", "具体问题2"]
    },
    ...
]

判定标准：
- "pass": 翻译准确、完整，无明显问题
- "fail": 有明显错误（漏翻、错翻、格式损坏、术语错误、回显原文、重复输出循环等），建议打回重翻
- "uncertain": 质量存疑但不确定是否错误，需人工审核

特别注意以下必须判为 "fail" 的情况：
- 译文直接将原文原封不动复制输出（回显原文），除非原文明显是不需要翻译的代码/标识符/数字
- 译文存在同一字符或短语的无限重复输出（如连续重复同一个字超过十次）

注意：
- 不要吹毛求疵，只关注明显问题
- 如果拿不准，返回"uncertain"而非"fail"
- 必须包含所有条目的检测结果，顺序与输入一致"""


def _get_prompts_dir() -> Path:
    """定位 data/prompts/ 目录，兼容开发环境和 PyInstaller 打包环境。"""
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


class QualityGateChecker(BaseChecker):
    """
    质量关卡检测器。

    调用轻量级LLM判断翻译是否存在明显质量问题，输出三态：
    - PASS: 通过，无大问题
    - FAIL: 失败，有明显质量问题（建议打回重翻）
    - UNCERTAIN: 无法判定（需人工审核）

    提示词配置从 data/prompts/quality_gate/{target_lang}.toml 加载，支持多语言扩展。
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
        self._prompts = self._load_prompts(game_profile, target_lang)

    def _load_prompts(self, game_profile: str, target_lang: str) -> dict:
        """从 TOML 文件加载提示词配置。"""
        prompts_dir = _get_prompts_dir()

        # 加载游戏和语言配置（用于变量替换）
        game_data = _load_toml(prompts_dir / "games" / f"{game_profile}.toml")
        lang_data = _load_toml(prompts_dir / "langs" / f"{target_lang}.toml")

        # 加载质量检测专用配置（按语言分离）
        qg_path = prompts_dir / "quality_gate" / f"{target_lang}.toml"
        qg_data = _load_toml(qg_path)

        # 构建变量上下文
        game = game_data.get("game", {})
        lang = lang_data.get("lang", {})

        ctx = {
            "game_name": game.get("name", "上古卷轴5：天际特别版（SSE）"),
            "source_lang": lang.get("source", "英文"),
            "target_lang": lang.get("target", "中文"),
        }

        single_cfg = qg_data.get("single_check", {})
        batch_cfg = qg_data.get("batch_check", {})

        return {
            "ctx": ctx,
            "single_system": single_cfg.get("system", _DEFAULT_SINGLE_SYSTEM).strip(),
            "single_user": single_cfg.get("user", _DEFAULT_SINGLE_USER).strip(),
            "batch_system": batch_cfg.get("system", _DEFAULT_BATCH_SYSTEM).strip(),
        }

    def _render(self, template: str, **extra) -> str:
        """将 $var 占位符替换为实际值，未知占位符保留原样。"""
        return Template(template).safe_substitute({**self._prompts["ctx"], **extra})

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

        # 使用模板渲染构建提示词
        user_content = self._render(
            self._prompts["single_user"],
            original=entry.original or "",
            translation=entry.translation or "",
            context=entry.context or "未知",
            terms=self._format_terms(terms),
        )
        system_content = self._render(self._prompts["single_system"])

        try:
            response = self._llm.chat(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=500,
            )
            return self._parse_response(response)
        except Exception as e:
            # LLM调用失败，返回uncertain
            return QualityGateResult(
                verdict=QualityVerdict.UNCERTAIN,
                reason=f"检测失败: {e}",
                issues=["质量检测出错，建议人工审核"],
            )

    def _check_batch_internal(
        self, entries: list["TranslationEntry"]
    ) -> list[PostProcessIssue]:
        """批量检查内部实现。"""
        if not self._llm:
            return []

        # 过滤掉没有译文的条目
        valid_entries = [e for e in entries if e.translation]
        if not valid_entries:
            return []

        # 构建批量检测Prompt
        prompt = self._build_batch_prompt(valid_entries)
        system_content = self._render(self._prompts["batch_system"])

        try:
            response = self._llm.chat(
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2000,
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
        """构建批量检测的Prompt。"""
        lines = ["请对以下翻译条目进行质量检测，返回JSON数组格式的结果。\n"]
        lines.append("待检测条目：")
        lines.append("-" * 40)

        for entry in entries:
            lines.append(f"\n[ENTRY_ID: {entry.id}]")
            lines.append(f"原文：{entry.original or ''}")
            lines.append(f"译文：{entry.translation or ''}")
            lines.append(f"上下文：{entry.context or '未知'}")

        return "\n".join(lines)


    def _parse_batch_response(
        self, entries: list["TranslationEntry"], response: str
    ) -> list[PostProcessIssue]:
        """解析批量检测响应。"""
        import json
        import re

        try:
            # 提取JSON数组
            json_match = re.search(r"\[[\s\S]*\]", response)
            if json_match:
                data = json.loads(json_match.group())
            else:
                # 尝试直接解析整个响应
                data = json.loads(response)

            issues = []
            entry_map = {e.key: e for e in entries}

            for item in data:
                entry_id = item.get("entry_id", "")
                entry = entry_map.get(entry_id)
                if not entry:
                    continue

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

            return issues

        except json.JSONDecodeError:
            # 解析失败，降级为逐个检测
            return self._fallback_batch_check(entries, response)

    def _fallback_batch_check(
        self, entries: list["TranslationEntry"], response: str
    ) -> list[PostProcessIssue]:
        """批量解析失败时的降级处理：尝试文本匹配。"""
        issues = []
        text_lower = response.lower()

        # 简单启发式：如果响应包含"fail"或"错误"，将所有条目标记为uncertain
        has_fail_indicator = any(
            kw in text_lower for kw in ["fail", "错误", "问题", "不匹配"]
        )

        for entry in entries:
            if has_fail_indicator:
                result = QualityGateResult(
                    verdict=QualityVerdict.UNCERTAIN,
                    reason="批量解析异常，建议人工审核",
                    issues=["响应解析失败，请人工确认质量"],
                )
            else:
                # 没有明显的失败指示，假设通过
                result = QualityGateResult(
                    verdict=QualityVerdict.PASS,
                    reason="批量解析异常但无错误指示",
                    issues=[],
                )
            issues.extend(self._result_to_issues(entry, result))

        return issues

    def _get_relevant_terms(
        self, entry: "TranslationEntry"
    ) -> dict[str, str]:
        """获取与条目相关的术语。"""
        if not self._term_manager or not entry.original:
            return {}

        # 使用 term_manager 的子串扫描匹配获取原文中命中的术语
        return self._term_manager.match_terms([entry.original])

    def _format_terms(self, terms: dict[str, str]) -> str:
        """格式化术语表供Prompt使用。"""
        if not terms:
            return "无"
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
        except json.JSONDecodeError:
            # 解析失败，从文本判断
            text = response.lower()
            if "fail" in text or "错误" in text or "问题" in text:
                return QualityGateResult(
                    verdict=QualityVerdict.FAIL,
                    reason="LLM检测到问题（响应解析异常）",
                    issues=[response[:200]],
                )
            elif "pass" in text or "通过" in text or "正确" in text:
                return QualityGateResult(
                    verdict=QualityVerdict.PASS,
                    reason="LLM判定通过",
                    issues=[],
                )
            else:
                return QualityGateResult(
                    verdict=QualityVerdict.UNCERTAIN,
                    reason="无法解析LLM响应",
                    issues=[response[:200]],
                )

    def _result_to_issues(
        self, entry: "TranslationEntry", result: QualityGateResult
    ) -> list[PostProcessIssue]:
        """将质量检测结果转换为PostProcessIssue。"""
        if result.verdict == QualityVerdict.PASS:
            return []

        severity = "error" if result.verdict == QualityVerdict.FAIL else "warning"
        suggestion = (
            "建议重新翻译"
            if result.verdict == QualityVerdict.FAIL
            else "建议人工审核"
        )

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
