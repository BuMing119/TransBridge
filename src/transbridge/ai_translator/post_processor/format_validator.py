"""
格式验证器：验证格式标记、占位符、引号等的完整性。
"""

import re
from typing import TYPE_CHECKING

from .base import BaseChecker, PostProcessIssue

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry


class FormatValidator(BaseChecker):
    """
    格式验证器。

    检查规则：
    1. 占位符完整性：%s、%d、{0}等数量和顺序与原文一致
    2. 格式标记保留：<br>、[pagebreak]、\n等被正确保留
    3. 引号匹配：引号、方括号、圆括号正确闭合
    4. 非法字符：过滤可能导致游戏崩溃的控制字符
    """

    # 占位符正则模式
    PLACEHOLDER_PATTERNS = [
        (r"%[sdifouxXeEgGcCrR]", "printf"),  # %s, %d, %f 等
        (r"\{\d+\}", "csharp"),  # {0}, {1} 等
        (r"\{\{.*?\}\}", "mustache"),  # {{variable}} 等
    ]

    # 格式标记
    FORMAT_TAGS = ["<br>", "<br/>", "<br />", "[pagebreak]", "\\n", "\\r\\n"]

    @property
    def name(self) -> str:
        return "format_validator"

    def check(self, entry: "TranslationEntry") -> list[PostProcessIssue]:
        """
        检查单个条目的格式完整性。

        Args:
            entry: 待检查的翻译条目

        Returns:
            发现的格式问题列表
        """
        issues = []

        if not entry.translation:
            return issues

        original = entry.original or ""
        translation = entry.translation

        # 占位符检查
        issues.extend(self._check_placeholders(entry, original, translation))

        # 格式标记检查
        issues.extend(self._check_format_tags(entry, original, translation))

        # 引号匹配检查
        issues.extend(self._check_quotes(entry, translation))

        # 非法字符检查
        issues.extend(self._check_illegal_chars(entry, translation))

        return issues

    def _check_placeholders(self, entry: "TranslationEntry", original: str, translation: str) -> list[PostProcessIssue]:
        """检查占位符是否匹配。"""
        issues = []

        # 提取原文和译文中的占位符
        original_placeholders = self._extract_placeholders(original)
        translation_placeholders = self._extract_placeholders(translation)

        # 检查数量是否一致
        if len(original_placeholders) != len(translation_placeholders):
            issues.append(
                PostProcessIssue(
                    entry_id=entry.id,
                    issue_type=PostProcessIssue.PLACEHOLDER_MISMATCH,
                    severity="error",
                    message=(
                        f"占位符数量不匹配：原文 {len(original_placeholders)} 个，"
                        f"译文 {len(translation_placeholders)} 个"
                    ),
                    original=original,
                    translation=translation,
                    suggestion=f"请确保译文包含以下占位符: {original_placeholders}",
                )
            )
            return issues

        # 检查类型和顺序是否一致
        for i, (orig, trans) in enumerate(zip(original_placeholders, translation_placeholders)):
            if orig != trans:
                issues.append(
                    PostProcessIssue(
                        entry_id=entry.id,
                        issue_type=PostProcessIssue.PLACEHOLDER_MISMATCH,
                        severity="error",
                        message=f"第 {i + 1} 个占位符不匹配：原文 '{orig}'，译文 '{trans}'",
                        original=original,
                        translation=translation,
                        suggestion=f"请将 '{trans}' 改为 '{orig}'",
                    )
                )

        return issues

    def _extract_placeholders(self, text: str) -> list[str]:
        """
        从文本中提取所有占位符。

        Args:
            text: 待检查的文本

        Returns:
            按顺序排列的占位符列表
        """
        placeholders = []

        for pattern, _ in self.PLACEHOLDER_PATTERNS:
            matches = re.finditer(pattern, text)
            for match in matches:
                placeholders.append((match.start(), match.group()))

        # 按位置排序
        placeholders.sort(key=lambda x: x[0])
        return [p[1] for p in placeholders]

    def _check_format_tags(self, entry: "TranslationEntry", original: str, translation: str) -> list[PostProcessIssue]:
        """检查格式标记是否完整保留。"""
        issues = []

        for tag in self.FORMAT_TAGS:
            original_count = original.count(tag)
            translation_count = translation.count(tag)

            if original_count > translation_count:
                issues.append(
                    PostProcessIssue(
                        entry_id=entry.id,
                        issue_type=PostProcessIssue.FORMAT_TAG_BROKEN,
                        severity="warning",
                        message=f"格式标记 '{tag}' 缺失：原文 {original_count} 处，译文 {translation_count} 处",
                        original=original,
                        translation=translation,
                        suggestion=f"请确保译文包含 {original_count} 处 '{tag}' 标记",
                    )
                )
            elif original_count < translation_count:
                issues.append(
                    PostProcessIssue(
                        entry_id=entry.id,
                        issue_type=PostProcessIssue.FORMAT_TAG_BROKEN,
                        severity="warning",
                        message=f"格式标记 '{tag}' 过多：原文 {original_count} 处，译文 {translation_count} 处",
                        original=original,
                        translation=translation,
                        suggestion=f"请检查译文是否错误添加了 '{tag}' 标记",
                    )
                )

        return issues

    def _check_quotes(self, entry: "TranslationEntry", translation: str) -> list[PostProcessIssue]:
        """检查引号是否正确匹配。"""
        issues = []

        # 定义需要匹配的括号对
        bracket_pairs = [
            ('"', '"'),  # 双引号
            ("'", "'"),  # 单引号
            ("(", ")"),  # 圆括号
            ("[", "]"),  # 方括号
            ("{", "}"),  # 花括号
            ("「", "」"),  # 日式引号
            ("『", "』"),  # 日式双引号
        ]

        for open_char, close_char in bracket_pairs:
            open_count = translation.count(open_char)
            close_count = translation.count(close_char)

            # 处理相同开闭符号的情况（如双引号）
            if open_char == close_char:
                if open_count % 2 != 0:
                    issues.append(
                        PostProcessIssue(
                            entry_id=entry.id,
                            issue_type=PostProcessIssue.QUOTE_MISMATCH,
                            severity="warning",
                            message=f"引号 '{open_char}' 未正确闭合（共 {open_count} 个，应为偶数）",
                            original=entry.original or "",
                            translation=translation,
                            suggestion=f"请检查 '{open_char}' 引号是否成对出现",
                        )
                    )
            else:
                # 处理不同开闭符号的情况
                if open_count != close_count:
                    issues.append(
                        PostProcessIssue(
                            entry_id=entry.id,
                            issue_type=PostProcessIssue.QUOTE_MISMATCH,
                            severity="warning",
                            message=f"括号不匹配：'{open_char}' 有 {open_count} 个，'{close_char}' 有 {close_count} 个",
                            original=entry.original or "",
                            translation=translation,
                            suggestion=f"请确保 '{open_char}' 和 '{close_char}' 成对出现",
                        )
                    )

        # 检查嵌套是否正确（使用栈）
        stack_issues = self._check_nested_brackets(translation)
        for issue_msg in stack_issues:
            issues.append(
                PostProcessIssue(
                    entry_id=entry.id,
                    issue_type=PostProcessIssue.QUOTE_MISMATCH,
                    severity="warning",
                    message=issue_msg,
                    original=entry.original or "",
                    translation=translation,
                    suggestion="请检查括号嵌套顺序",
                )
            )

        return issues

    def _check_nested_brackets(self, text: str) -> list[str]:
        """
        使用栈检查括号是否正确嵌套。

        Args:
            text: 待检查的文本

        Returns:
            错误消息列表
        """
        issues = []
        stack = []

        # 定义括号映射
        bracket_map = {
            ")": "(",
            "]": "[",
            "}": "{",
            "」": "「",
            "』": "『",
        }
        open_brackets = set(["(", "[", "{", "「", "『"])

        for i, char in enumerate(text):
            if char in open_brackets:
                stack.append((char, i))
            elif char in bracket_map:
                if not stack:
                    issues.append(f"位置 {i} 处出现未匹配的闭合括号 '{char}'")
                else:
                    last_open, last_pos = stack.pop()
                    expected_open = bracket_map[char]
                    if last_open != expected_open:
                        issues.append(f"位置 {last_pos} 的 '{last_open}' 与位置 {i} 的 '{char}' 不匹配")

        # 检查未闭合的括号
        for char, pos in stack:
            issues.append(f"位置 {pos} 处的 '{char}' 未闭合")

        return issues

    def _check_illegal_chars(self, entry: "TranslationEntry", translation: str) -> list[PostProcessIssue]:
        """检查是否存在非法字符。"""
        issues = []

        # 定义可能导致问题的字符
        illegal_ranges = [
            (0x00, 0x08, "控制字符"),  # NUL-BS
            (0x0B, 0x0C, "控制字符"),  # VT-FF
            (0x0E, 0x1F, "控制字符"),  # SO-US
            (0x7F, 0x9F, "控制字符"),  # DEL-APC
        ]

        illegal_chars = []
        for char in translation:
            code = ord(char)
            for start, end, desc in illegal_ranges:
                if start <= code <= end:
                    illegal_chars.append((char, code, desc))
                    break

        if illegal_chars:
            char_details = ", ".join([f"'{c}' (U+{code:04X})" for c, code, _ in illegal_chars])
            issues.append(
                PostProcessIssue(
                    entry_id=entry.id,
                    issue_type=PostProcessIssue.FORMAT_TAG_BROKEN,
                    severity="error",
                    message=f"发现非法字符: {char_details}",
                    original=entry.original or "",
                    translation=translation,
                    suggestion="请移除这些控制字符，它们可能导致游戏崩溃或显示异常",
                )
            )

        # 检查可能导致编码问题的字符
        # 例如：BOM标记、零宽字符等
        zero_width_chars = ["\ufeff", "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"]
        found_zero_width = []
        for char in zero_width_chars:
            if char in translation:
                found_zero_width.append(f"U+{ord(char):04X}")

        if found_zero_width:
            issues.append(
                PostProcessIssue(
                    entry_id=entry.id,
                    issue_type=PostProcessIssue.FORMAT_TAG_BROKEN,
                    severity="warning",
                    message=f"发现零宽字符: {', '.join(found_zero_width)}",
                    original=entry.original or "",
                    translation=translation,
                    suggestion="这些字符通常不可见，可能导致显示问题，建议移除",
                )
            )

        return issues
