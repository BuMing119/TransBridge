"""
一致性检查器：检查术语一致性。
"""

import re
from .base import BaseChecker, PostProcessIssue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry
    from ..term_database import TermEntry


class ConsistencyChecker(BaseChecker):
    """
    一致性检查器。

    检查规则：
    - 术语命中检查：原文中的术语是否在译文中使用了标准译法

    术语库来源：
    - 从硬盘加载合并后的术语缓存：data/ai_translator/{esp_stem}/cache/merged_terms.json
    - 该缓存由 TermDatabaseManager 在加载术语时生成
    """

    @property
    def name(self) -> str:
        return "consistency_checker"

    def __init__(self, esp_path: str | None = None):
        """
        初始化。

        Args:
            esp_path: ESP 文件路径，用于定位术语缓存目录
        """
        self._esp_path = esp_path
        # 缓存加载后的术语列表
        self._terms_cache: list["TermEntry"] = []
        self._terms_loaded = False

    def _load_terms(self) -> None:
        """从硬盘加载合并后的术语缓存。"""
        if self._terms_loaded:
            return

        if not self._esp_path:
            self._terms_loaded = True
            return

        # 直接从硬盘加载合并缓存，无需创建 TermDatabaseManager
        from ..term_database import TermDatabaseManager
        self._terms_cache = TermDatabaseManager.load_merged_cache(self._esp_path)
        self._terms_loaded = True

    def _get_matched_terms(self, original: str) -> dict[str, tuple[str, str]]:
        """
        获取原文中匹配的所有术语（高置信度匹配）。

        匹配策略（保守策略）：
        1. 精确全等匹配
        2. 词边界子串匹配（确保术语是完整单词，不是其他词的一部分）

        同时检查主术语和所有变体，返回匹配到的形式及其对应的主术语信息。

        Args:
            original: 原文

        Returns:
            {匹配到的形式: (主术语, 译文)} 字典
            匹配到的形式可能是主术语本身，也可能是某个变体
        """
        self._load_terms()
        matched: dict[str, tuple[str, str]] = {}

        for entry in self._terms_cache:
            # 检查主术语
            if self._is_term_match(original, entry.term, entry.case_sensitive):
                matched[entry.term] = (entry.term, entry.translation)
                continue

            # 检查变体（只有当主术语未匹配时才检查）
            for variant in entry.variants:
                if not variant:
                    continue
                if self._is_term_match(original, variant, entry.case_sensitive):
                    matched[variant] = (entry.term, entry.translation)
                    break  # 该 entry 已匹配，跳过后续变体

        return matched

    def _is_term_match(
        self, text: str, term: str, case_sensitive: bool = False
    ) -> bool:
        """
        检查单个术语/变体是否匹配原文。

        匹配策略：
        1. 精确全等匹配
        2. 词边界子串匹配

        Args:
            text: 待检查的原文
            term: 要匹配的术语或变体
            case_sensitive: 是否区分大小写

        Returns:
            是否匹配
        """
        if not term:
            return False

        if case_sensitive:
            # 精确全等
            if text == term:
                return True
            # 词边界子串匹配
            return self._is_word_boundary_match(text, term, True)
        else:
            # 不区分大小写的精确全等
            if text.lower() == term.lower():
                return True
            # 词边界子串匹配
            return self._is_word_boundary_match(text, term, False)

    def _is_word_boundary_match(
        self, text: str, term: str, case_sensitive: bool = False
    ) -> bool:
        """
        检查术语在文本中是否为词边界匹配。

        词边界定义：
        - 术语位于文本开头或前一个字符是非单词字符
        - 术语位于文本末尾或后一个字符是非单词字符

        Args:
            text: 待检查的文本
            term: 要匹配的术语
            case_sensitive: 是否区分大小写

        Returns:
            是否为词边界匹配
        """
        if not term:
            return False

        flags = 0 if case_sensitive else re.IGNORECASE
        # 构建词边界正则：术语前后必须是字符串边界或非单词字符
        # \b 在 Python 中匹配的是单词字符(\w)和非单词字符之间的边界
        pattern = r"\b" + re.escape(term) + r"\b"

        return bool(re.search(pattern, text, flags))

    def check(self, entry: "TranslationEntry") -> list[PostProcessIssue]:
        """
        检查单个条目的术语一致性。

        支持主术语和变体匹配：
        - 原文匹配主术语时，提示使用主术语的标准译法
        - 原文匹配变体时，同样提示使用主术语的标准译法（并注明匹配到的变体）

        Args:
            entry: 待检查的翻译条目

        Returns:
            发现的术语一致性问题列表
        """
        issues = []

        if not entry.translation:
            # 未翻译的条目跳过检查
            return issues

        # 获取原文中匹配的所有术语（包括变体）
        # 返回格式: {匹配到的形式: (主术语, 译文)}
        matched_terms = self._get_matched_terms(entry.original)

        # 按主术语去重，避免同一术语的多个变体产生重复报告
        reported_main_terms: set[str] = set()

        for matched_form, (main_term, std_translation) in matched_terms.items():
            # 如果该主术语已报告过，跳过
            if main_term in reported_main_terms:
                continue

            # 检查译文中是否包含标准译法
            if std_translation not in entry.translation:
                # 区分主术语匹配和变体匹配的消息
                if matched_form == main_term:
                    message = f"术语 '{main_term}' 标准译法为 '{std_translation}'，译文未使用"
                else:
                    message = f"术语 '{main_term}'（变体 '{matched_form}'）标准译法为 '{std_translation}'，译文未使用"

                # 生成警告级别的问题
                issues.append(
                    PostProcessIssue(
                        entry_id=entry.id,
                        issue_type=PostProcessIssue.TERM_MISMATCH,
                        severity="warning",
                        message=message,
                        original=entry.original,
                        translation=entry.translation,
                        suggestion=std_translation,
                    )
                )
                reported_main_terms.add(main_term)

        return issues

    def check_batch(self, entries: list["TranslationEntry"]) -> list[PostProcessIssue]:
        """
        批量检查条目一致性。

        Args:
            entries: 待检查的条目列表

        Returns:
            发现的一致性问题列表
        """
        # 当前仅执行单条目的术语检查
        issues = []
        for entry in entries:
            issues.extend(self.check(entry))
        return issues
