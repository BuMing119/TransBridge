"""
后处理主控器，协调各检查器执行。

四阶段流程：
1. 检测（Checker）- 发现问题
2. 修复（LLMRefiner）- 修复问题并润色
3. 裁决（LLMArbiter）- 判定pass/reject/pending
4. 执行（Action）- 更新条目stage
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .base import BaseChecker, PostProcessResult, PostProcessIssue
from .consistency_checker import ConsistencyChecker
from .format_validator import FormatValidator
from .quality_gate import QualityGateChecker

if TYPE_CHECKING:
    from ...converter.translation_entry import TranslationEntry
    from ...converter.translation_entry_collection import TranslationEntryCollection
    from ..llm_client import LLMClient
    from ..term_database import TermDatabaseManager
    from .llm_refiner import RefineResult
    from .polisher import PolishResult
    from .llm_arbiter import ArbiterDecision


@dataclass
class PostProcessorConfig:
    """后处理器配置。"""

    # ── 阶段1: 检测 ──────────────────────────────────────────────────────
    enable_consistency_check: bool = True
    enable_format_validation: bool = True
    enable_quality_gate: bool = True

    # 质量关卡检测批大小
    quality_gate_batch_size: int = 10

    # ── 阶段2a: 修复（LLMRefiner）─────────────────────────────────────────
    enable_refinement: bool = True
    refinement_batch_size: int = 5

    # ── 阶段2b: 润色（LLMPolisher）────────────────────────────────────────
    enable_polish: bool = False
    polish_scope: str = "all"  # "all" | "passed" | "has_issues"
    polish_level: str = "moderate"  # light/moderate/aggressive
    polish_batch_size: int = 5

    # ── 阶段3: 裁决（LLMArbiter）─────────────────────────────────────────
    enable_llm_arbitration: bool = True
    strict_arbitration: bool = False  # True: uncertain -> reject
    arbitration_batch_size: int = 10

    # ── 通用配置 ──────────────────────────────────────────────────────────
    game_profile: str = "skyrim_se"
    target_lang: str = "zh_CN"

    # 遗留配置（向后兼容）
    auto_fix: bool = False
    reset_stage_on_error: bool = False

    @classmethod
    def from_llm_config(cls) -> "PostProcessorConfig":
        """从 LLMConfig 加载配置创建 PostProcessorConfig。"""
        from ...paratranz.config_manager import LLMConfig

        llm_config = LLMConfig.load_from_file()
        return cls(
            game_profile=llm_config.game_profile,
            target_lang=llm_config.target_lang,
        )


@dataclass
class PostProcessExecutionResult:
    """后处理执行结果统计。"""

    passed: int = 0
    rejected: int = 0
    pending: int = 0
    errors: int = 0


class PostProcessor:
    """
    后处理主控器。

    五阶段流程：
    1. 检测：ConsistencyChecker, FormatValidator, QualityGateChecker
    2. 修复：LLMRefiner（修复问题）
    3. 润色：LLMPolisher（可选，提升质量）
    4. 裁决：LLMArbiter（pass/reject/pending）
    5. 执行：根据裁决结果更新条目
    """

    def __init__(self, config: PostProcessorConfig | None = None):
        """
        初始化后处理器。

        Args:
            config: 后处理器配置
        """
        self._config = config or PostProcessorConfig()
        self._checkers: list[BaseChecker] = []
        self._refiner = None
        self._polisher = None
        self._arbiter = None

    def register_checker(self, checker: BaseChecker) -> None:
        """
        注册自定义检查器。

        Args:
            checker: 检查器实例
        """
        self._checkers.append(checker)

    def register_default_checkers(
        self,
        term_manager: "TermDatabaseManager | None" = None,
        llm_client: "LLMClient | None" = None,
    ) -> None:
        """
        注册默认检查器集合（检测器 + 修复者 + 裁决者）。

        Args:
            term_manager: TermDatabaseManager 实例
            llm_client: LLMClient 实例（修复和裁决需要）
        """
        # ── 阶段1: 检测器 ─────────────────────────────────────────────────
        if self._config.enable_consistency_check:
            self.register_checker(ConsistencyChecker(term_manager))

        if self._config.enable_format_validation:
            self.register_checker(FormatValidator())

        if self._config.enable_quality_gate and llm_client:
            self.register_checker(
                QualityGateChecker(
                    llm_client=llm_client,
                    term_manager=term_manager,
                    batch_size=self._config.quality_gate_batch_size,
                    game_profile=self._config.game_profile,
                    target_lang=self._config.target_lang,
                )
            )

        # ── 阶段2a: 修复者（LLMRefiner）───────────────────────────────────
        if self._config.enable_refinement and llm_client:
            from .llm_refiner import LLMRefiner

            self._refiner = LLMRefiner(
                llm_client=llm_client,
                term_manager=term_manager,
                game_profile=self._config.game_profile,
                target_lang=self._config.target_lang,
            )

        # ── 阶段2b: 润色者（LLMPolisher）───────────────────────────────────
        if self._config.enable_polish and llm_client:
            from .polisher import LLMPolisher

            self._polisher = LLMPolisher(
                llm_client=llm_client,
                term_manager=term_manager,
                game_profile=self._config.game_profile,
                target_lang=self._config.target_lang,
                polish_level=self._config.polish_level,
            )

        # ── 阶段3: 裁决者（LLMArbiter）────────────────────────────────────
        if self._config.enable_llm_arbitration and llm_client:
            from .llm_arbiter import LLMArbiter

            self._arbiter = LLMArbiter(
                llm_client=llm_client,
                game_profile=self._config.game_profile,
                target_lang=self._config.target_lang,
                strict_mode=self._config.strict_arbitration,
            )

    def process(self, collection: "TranslationEntryCollection") -> PostProcessResult:
        """
        对整个集合执行后处理。

        Args:
            collection: 翻译条目集合

        Returns:
            后处理结果
        """
        entries = list(collection.entries.values())
        return self.process_entries(entries)

    def process_entries(
        self,
        entries: list["TranslationEntry"],
        progress_callback=None,
    ) -> PostProcessResult:
        """
        对指定条目执行后处理（五阶段流程）。

        Args:
            entries: 待处理的条目列表
            progress_callback: 进度回调函数 (phase, current, total, message)

        Returns:
            后处理结果
        """
        result = PostProcessResult(total_checked=len(entries))

        def _progress(phase: str, current: int, total: int, message: str = ""):
            if progress_callback:
                progress_callback(phase, current, total, message)

        # ── 阶段1: 检测 ────────────────────────────────────────────────────
        _progress("detect", 0, len(entries), "开始检测...")

        for checker in self._checkers:
            if isinstance(checker, ConsistencyChecker):
                issues = checker.check_batch(entries)
            else:
                issues = []
                for entry in entries:
                    issues.extend(checker.check(entry))

            for issue in issues:
                result.add_issue(issue)

        # 按 entry_id 分组问题
        issues_by_entry = self._group_issues_by_entry(result.issues)

        _progress("detect", len(entries), len(entries), f"检测完成，发现 {len(result.issues)} 个问题")

        # ── 阶段2a: 修复 ────────────────────────────────────────────────────
        refine_results: dict[str, "RefineResult"] = {}

        if self._refiner and issues_by_entry:
            entries_to_refine = [e for e in entries if e.id in issues_by_entry]
            total = len(entries_to_refine)

            _progress("refine", 0, total, f"开始修复 {total} 个条目...")

            # 使用批量修复
            for i in range(0, total, self._config.refinement_batch_size):
                batch = entries_to_refine[i : i + self._config.refinement_batch_size]
                batch_issues = {e.id: issues_by_entry.get(e.id, []) for e in batch}

                batch_results = self._refiner.refine_batch(batch, batch_issues)
                refine_results.update(batch_results)

                _progress("refine", min(i + len(batch), total), total, f"已修复 {min(i + len(batch), total)}/{total}")

        # ── 阶段2b: 润色 ────────────────────────────────────────────────────
        polish_results: dict[str, "PolishResult"] = {}

        if self._polisher and self._config.enable_polish:
            entries_to_polish = self._select_entries_for_polish(
                entries, issues_by_entry, refine_results
            )
            total = len(entries_to_polish)

            if total > 0:
                _progress("polish", 0, total, f"开始润色 {total} 个条目...")

                # 使用批量润色
                for i in range(0, total, self._config.polish_batch_size):
                    batch = entries_to_polish[i : i + self._config.polish_batch_size]

                    batch_results = self._polisher.polish_batch(batch)
                    polish_results.update(batch_results)

                    _progress("polish", min(i + len(batch), total), total, f"已润色 {min(i + len(batch), total)}/{total}")

        # ── 阶段3: 裁决 ────────────────────────────────────────────────────
        decisions: dict[str, "ArbiterDecision"] = {}

        if self._arbiter:
            from .llm_arbiter import ArbitrationContext

            total = len(entries)
            _progress("arbitrate", 0, total, "开始裁决...")

            contexts = []
            for entry in entries:
                ctx = ArbitrationContext(
                    entry=entry,
                    original_issues=issues_by_entry.get(entry.id, []),
                    refine_result=refine_results.get(entry.id),
                    polish_result=polish_results.get(entry.id),
                    quality_gate_verdict=self._get_quality_gate_verdict(
                        entry.id, result.issues
                    ),
                )
                contexts.append(ctx)

            # 使用批量裁决
            for i in range(0, len(contexts), self._config.arbitration_batch_size):
                batch = contexts[i : i + self._config.arbitration_batch_size]
                batch_decisions = self._arbiter.arbitrate_batch(batch)
                decisions.update(batch_decisions)

                processed = min(i + len(batch), len(contexts))
                _progress("arbitrate", processed, total, f"已裁决 {processed}/{total}")

        else:
            # 无裁决者：基于规则简单判定
            decisions = self._rule_based_decide(
                entries, issues_by_entry, refine_results, polish_results
            )

        # ── 阶段4: 执行 ────────────────────────────────────────────────────
        _progress("execute", 0, len(entries), "执行裁决结果...")

        execution_result = self._execute_decisions(
            entries, refine_results, polish_results, decisions, result
        )

        _progress(
            "execute",
            len(entries),
            len(entries),
            f"执行完成：通过 {execution_result.passed}，打回 {execution_result.rejected}，待审 {execution_result.pending}",
        )

        # 保存执行统计
        result.execution_result = execution_result

        return result

    def _group_issues_by_entry(
        self, issues: list[PostProcessIssue]
    ) -> dict[str, list[PostProcessIssue]]:
        """按 entry_id 分组问题。"""
        result: dict[str, list[PostProcessIssue]] = {}
        for issue in issues:
            if issue.entry_id not in result:
                result[issue.entry_id] = []
            result[issue.entry_id].append(issue)
        return result

    def _select_entries_for_polish(
        self,
        entries: list["TranslationEntry"],
        issues_by_entry: dict[str, list[PostProcessIssue]],
        refine_results: dict[str, "RefineResult"],
    ) -> list["TranslationEntry"]:
        """
        根据 polish_scope 配置选择需要润色的条目。

        Args:
            entries: 所有条目
            issues_by_entry: 问题分组
            refine_results: 修复结果

        Returns:
            需要润色的条目列表
        """
        scope = self._config.polish_scope

        if scope == "all":
            # 润色所有条目
            return [e for e in entries if e.translation]
        elif scope == "passed":
            # 只润色无问题的条目
            return [
                e for e in entries
                if e.translation and e.id not in issues_by_entry
            ]
        elif scope == "has_issues":
            # 只润色有问题的条目（修复后润色）
            return [
                e for e in entries
                if e.translation and e.id in issues_by_entry and e.id in refine_results
            ]
        else:
            # 默认润色所有
            return [e for e in entries if e.translation]

    def _get_quality_gate_verdict(
        self, entry_id: str, issues: list[PostProcessIssue]
    ) -> str | None:
        """获取质量关卡对某个条目的判定。"""
        for issue in issues:
            if issue.entry_id == entry_id and issue.issue_type == PostProcessIssue.LOW_QUALITY:
                # 从message中提取verdict
                if "[fail]" in issue.message.lower():
                    return "fail"
                elif "[pass]" in issue.message.lower():
                    return "pass"
                elif "[uncertain]" in issue.message.lower():
                    return "uncertain"
        return None

    def _rule_based_decide(
        self,
        entries: list["TranslationEntry"],
        issues_by_entry: dict[str, list[PostProcessIssue]],
        refine_results: dict[str, "RefineResult"],
        polish_results: dict[str, "PolishResult"] | None = None,
    ) -> dict[str, "ArbiterDecision"]:
        """基于规则的简单裁决（无LLM裁决者时使用）。"""
        from .llm_arbiter import ArbiterDecision

        decisions = {}
        polish_results = polish_results or {}

        for entry in entries:
            issues = issues_by_entry.get(entry.id, [])
            refine = refine_results.get(entry.id)
            polish = polish_results.get(entry.id)

            has_errors = any(i.severity == "error" for i in issues)
            has_warnings = any(i.severity == "warning" for i in issues)

            if not issues:
                # 无问题 -> 通过
                decisions[entry.id] = ArbiterDecision(
                    entry_id=entry.id,
                    verdict="pass",
                    reason="无检测到的问题",
                    confidence=1.0,
                    suggested_action="无需操作",
                )
            elif refine and refine.confidence > 0.8 and not has_errors:
                # 修复信心度高且无error -> 通过
                decisions[entry.id] = ArbiterDecision(
                    entry_id=entry.id,
                    verdict="pass",
                    reason=f"修复信心度高({refine.confidence:.2f})",
                    confidence=refine.confidence,
                    suggested_action="接受修复后译文",
                )
            elif has_errors:
                # 有error -> 根据strict模式
                if self._config.strict_arbitration:
                    decisions[entry.id] = ArbiterDecision(
                        entry_id=entry.id,
                        verdict="reject",
                        reason="存在未修复的严重问题",
                        confidence=0.8,
                        suggested_action="打回重翻",
                    )
                else:
                    decisions[entry.id] = ArbiterDecision(
                        entry_id=entry.id,
                        verdict="pending",
                        reason="存在需要关注的问题",
                        confidence=0.6,
                        suggested_action="人工审核",
                    )
            else:
                # 只有warning -> pending
                decisions[entry.id] = ArbiterDecision(
                    entry_id=entry.id,
                    verdict="pending",
                    reason="存在警告级别的问题",
                    confidence=0.7,
                    suggested_action="人工审核",
                )

        return decisions

    def _execute_decisions(
        self,
        entries: list["TranslationEntry"],
        refine_results: dict[str, "RefineResult"],
        polish_results: dict[str, "PolishResult"],
        decisions: dict[str, "ArbiterDecision"],
        result: PostProcessResult,
    ) -> PostProcessExecutionResult:
        """
        执行裁决结果。

        优先级：润色结果 > 修复结果 > 原译文

        verdict=pass    -> stage=1 (检查通过) + 写入最终译文
        verdict=reject  -> stage=0 (打回重翻) + 记录原因
        verdict=pending -> stage=2 (待人工审核) + 记录最终版本和裁决信息
        """
        from ...converter.translation_entry import TranslationEntry

        exec_result = PostProcessExecutionResult()

        for entry in entries:
            decision = decisions.get(entry.id)
            if not decision:
                continue

            refined = refine_results.get(entry.id)
            polished = polish_results.get(entry.id)

            # 确定最终译文（优先级：润色 > 修复 > 原文）
            if polished and polished.polished_translation:
                translation_to_use = polished.polished_translation
            elif refined and refined.refined_translation:
                translation_to_use = refined.refined_translation
            else:
                translation_to_use = entry.translation

            if decision.verdict == "pass":
                # 接受最终译文
                updated = TranslationEntry(
                    id=entry.id,
                    key=entry.key,
                    original=entry.original,
                    translation=translation_to_use,
                    stage=1,  # 检查通过
                    context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                    string_id=entry.string_id,
                    dsd_type=entry.dsd_type,
                    dsd_index=entry.dsd_index,
                    editor_id=entry.editor_id,
                )
                entry._replace(updated)
                exec_result.passed += 1

            elif decision.verdict == "reject":
                # 打回重翻
                updated = TranslationEntry(
                    id=entry.id,
                    key=entry.key,
                    original=entry.original,
                    translation=entry.translation,
                    stage=0,  # 未翻译
                    context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                    string_id=entry.string_id,
                    dsd_type=entry.dsd_type,
                    dsd_index=entry.dsd_index,
                    editor_id=entry.editor_id,
                )
                entry._replace(updated)
                exec_result.rejected += 1

            elif decision.verdict == "pending":
                # 保留待审（stage保持2）
                # 可以在这里添加标记，记录有修复/润色版本待确认
                exec_result.pending += 1

        return exec_result

    # ── 向后兼容的方法 ────────────────────────────────────────────────────

    def _auto_fix(
        self, entries: list["TranslationEntry"], issues: list
    ) -> int:
        """
        尝试自动修复可修复的问题（遗留方法，现在由LLMRefiner处理）。

        Args:
            entries: 条目列表
            issues: 问题列表

        Returns:
            成功修复的问题数
        """
        import re
        from ...converter.translation_entry import TranslationEntry

        fixed_count = 0
        entry_map = {e.id: e for e in entries}

        for issue in issues:
            if issue.entry_id not in entry_map:
                continue

            entry = entry_map[issue.entry_id]
            if not entry.translation:
                continue

            original_translation = entry.translation
            fixed_translation = original_translation

            # 修复 1: 占位符空格问题
            if issue.issue_type == PostProcessIssue.PLACEHOLDER_MISMATCH:
                fixed_translation = self._fix_placeholder_spaces(fixed_translation)

            # 修复 2: 格式标记大小写
            if issue.issue_type == PostProcessIssue.FORMAT_TAG_BROKEN:
                fixed_translation = self._fix_format_tag_case(fixed_translation)

            # 修复 3: 简单引号补全
            if issue.issue_type == PostProcessIssue.QUOTE_MISMATCH:
                fixed_translation = self._fix_simple_quotes(
                    fixed_translation, issue.original
                )

            # 如果有修复，更新条目
            if fixed_translation != original_translation:
                updated = TranslationEntry(
                    id=entry.id,
                    key=entry.key,
                    original=entry.original,
                    translation=fixed_translation,
                    stage=entry.stage,
                    context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                    string_id=entry.string_id,
                    dsd_type=entry.dsd_type,
                    dsd_index=entry.dsd_index,
                    editor_id=entry.editor_id,
                )
                entry_map[issue.entry_id] = updated
                fixed_count += 1

        return fixed_count

    def _fix_placeholder_spaces(self, text: str) -> str:
        """修复占位符中的空格问题（如 % s -> %s）。"""
        import re

        patterns = [
            (r"%\s+s", "%s"),
            (r"%\s+d", "%d"),
            (r"%\s+f", "%f"),
            (r"%\s+g", "%g"),
            (r"%\s+x", "%x"),
            (r"%\s+X", "%X"),
            (r"%\s+c", "%c"),
            (r"%\s+r", "%r"),
        ]
        result = text
        for pattern, replacement in patterns:
            result = re.sub(pattern, replacement, result)
        return result

    def _fix_format_tag_case(self, text: str) -> str:
        """统一格式标记为小写。"""
        replacements = [
            ("<BR>", "<br>"),
            ("<BR/>", "<br/>"),
            ("<BR />", "<br />"),
            ("<B>", "<b>"),
            ("<I>", "<i>"),
            ("<U>", "<u>"),
        ]
        result = text
        for old, new in replacements:
            result = result.replace(old, new)
        return result

    def _fix_simple_quotes(self, translation: str, original: str) -> str:
        """简单引号补全。"""
        if not original or not translation:
            return translation

        orig_start, orig_end = original[0], original[-1]
        trans_start, trans_end = translation[0], translation[-1]

        result = translation

        if orig_start == '"' and orig_end == '"':
            if trans_start != '"' and trans_end != '"':
                result = '"' + result + '"'
            elif trans_start != '"':
                result = '"' + result
            elif trans_end != '"':
                result = result + '"'
        elif orig_start == "'" and orig_end == "'":
            if trans_start != "'" and trans_end != "'":
                result = "'" + result + "'"
            elif trans_start != "'":
                result = "'" + result
            elif trans_end != "'":
                result = result + "'"

        return result

    def update_entry_stages(
        self,
        collection: "TranslationEntryCollection",
        result: PostProcessResult,
    ) -> dict[str, int]:
        """
        根据后处理结果更新条目的 stage（遗留方法，现在由_execute_decisions处理）。

        Args:
            collection: 翻译条目集合
            result: 后处理结果

        Returns:
            统计信息
        """
        from ...converter.translation_entry import TranslationEntry

        stats = {"reset_to_untranslated": 0, "kept_for_review": 0}

        entry_issues: dict[str, list] = {}
        for issue in result.issues:
            if issue.entry_id not in entry_issues:
                entry_issues[issue.entry_id] = []
            entry_issues[issue.entry_id].append(issue)

        for entry_id, issues in entry_issues.items():
            entry = collection.get(entry_id)
            if entry is None:
                continue

            has_error = any(i.severity == "error" for i in issues)
            has_warning = any(i.severity == "warning" for i in issues)

            if has_error and self._config.reset_stage_on_error:
                updated = TranslationEntry(
                    id=entry.id,
                    key=entry.key,
                    original=entry.original,
                    translation=entry.translation,
                    stage=0,
                    context=entry.context,
                    form_id_with_plugin=entry.form_id_with_plugin,
                    string_id=entry.string_id,
                    dsd_type=entry.dsd_type,
                    dsd_index=entry.dsd_index,
                    editor_id=entry.editor_id,
                )
                collection.add(updated, overwrite=True)
                stats["reset_to_untranslated"] += 1
            elif has_error or has_warning:
                stats["kept_for_review"] += 1

        return stats
