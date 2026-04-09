"""
后处理器抽象基类，定义统一接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry


@dataclass
class PostProcessIssue:
    """后处理发现的问题。"""

    entry_id: str
    issue_type: str
    severity: str  # "error" | "warning" | "info"
    message: str
    original: str
    translation: str
    suggestion: str = ""

    # 问题类型常量
    TERM_MISMATCH = "term_mismatch"
    CROSS_ENTRY_INCONSISTENCY = "cross_entry_inconsistency"
    PLACEHOLDER_MISSING = "placeholder_missing"
    PLACEHOLDER_MISMATCH = "placeholder_mismatch"
    FORMAT_TAG_BROKEN = "format_tag_broken"
    QUOTE_MISMATCH = "quote_mismatch"
    LOW_QUALITY = "low_quality"


@dataclass
class PostProcessResult:
    """后处理结果。"""

    total_checked: int = 0
    issue_count: int = 0
    issues: list[PostProcessIssue] = field(default_factory=list)
    auto_fixed: int = 0
    needs_review: list[str] = field(default_factory=list)

    def add_issue(self, issue: PostProcessIssue) -> None:
        """添加问题。"""
        self.issues.append(issue)
        self.issue_count += 1

    def merge(self, other: "PostProcessResult") -> None:
        """合并另一个结果。"""
        self.total_checked += other.total_checked
        self.issue_count += other.issue_count
        self.issues.extend(other.issues)
        self.auto_fixed += other.auto_fixed
        self.needs_review.extend(other.needs_review)


class BaseChecker(ABC):
    """后处理检查器抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """检查器名称。"""
        pass

    @abstractmethod
    def check(self, entry: "TranslationEntry") -> list[PostProcessIssue]:
        """
        检查单个条目，返回问题列表。

        Args:
            entry: 待检查的翻译条目

        Returns:
            发现的问题列表（无问题返回空列表）
        """
        pass

    def check_batch(self, entries: list["TranslationEntry"]) -> list[PostProcessIssue]:
        """
        批量检查（用于需要跨条目分析的检验器）。

        默认实现为逐个调用 check()，子类可覆盖以实现批量优化。

        Args:
            entries: 待检查的条目列表

        Returns:
            发现的问题列表
        """
        issues = []
        for entry in entries:
            issues.extend(self.check(entry))
        return issues