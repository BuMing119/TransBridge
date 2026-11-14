# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Task-oriented copy derived from the existing authoritative asset snapshot."""

from dataclasses import dataclass

from transbridge.application.terminology.models import BuildCompleteness, BuildFreshness
from transbridge.application.terminology.workbench_status import DraftStatus


@dataclass(frozen=True, slots=True)
class TerminologyWorkflowState:
    title: str
    detail: str
    has_draft: bool = False
    has_version: bool = False
    review_count: int = 0
    conflict_count: int = 0
    can_publish: bool = False
    next_action: str = "build"

    def display_copy(self, pending=0):
        if pending:
            return (
                "正在保存处理结果…",
                f"还有 {pending} 项修改正在保存，完成后可以应用。",
            )
        return self.title, self.detail

    @classmethod
    def from_assets(cls, assets):
        has_draft = assets.draft is not None
        has_version = assets.version_ref is not None
        counts = assets.review_counts
        review = 0 if counts is None else counts.review_required_terms
        conflicts = 0 if counts is None else counts.unresolved_conflicts
        build = assets.build
        complete = build is not None and build.completeness == BuildCompleteness.FULL
        llm_incomplete = build is not None and str(getattr(build, "llm_status", "")) in {"partial", "unavailable"}
        complete = complete and not llm_incomplete
        current = build is not None and build.freshness == BuildFreshness.CURRENT
        if has_draft:
            count = (
                assets.draft.decision_count if isinstance(assets.draft, DraftStatus) else len(assets.draft.decisions)
            )
            title = "有差异需要处理" if review + conflicts else "结果已就绪，可以应用"
            detail = f"{count:,} 条待应用术语 · {review} 条译名变化 · {conflicts} 组冲突。"
            version = getattr(assets, "version", None)
            if isinstance(assets.draft, DraftStatus):
                added, unchanged = assets.draft.added_count, assets.draft.unchanged_count
                detail = f"新增 {added:,} 条 · 无变化 {unchanged:,} 条 · {review} 条译名待决定 · {conflicts} 组冲突。"
            elif version is not None or not has_version:
                previous = (
                    {}
                    if version is None
                    else {(item.normalized_original, item.scope): item for item in version.decisions}
                )
                added = unchanged = 0
                for item in assets.draft.decisions:
                    old = previous.get((item.normalized_original, item.scope))
                    if old is None and not item.suppressed:
                        added += 1
                    elif old is not None and (item.translation, item.suppressed, item.variants, item.notes) == (
                        old.translation,
                        old.suppressed,
                        old.variants,
                        old.notes,
                    ):
                        unchanged += 1
                detail = f"新增 {added:,} 条 · 无变化 {unchanged:,} 条 · {review} 条译名待决定 · {conflicts} 组冲突。"
            if llm_incomplete and current:
                title = "需要重试 LLM 抽取"
                next_action = "build"
                detail += "请检查提取详情并重试未完成批次；已完成结果保留。"
            elif not complete or not current:
                title = "需要重新提取"
                next_action = "build"
                detail += "结果不完整或已过期，请重新提取后应用。"
            elif conflicts:
                next_action = "conflicts"
                detail += "请选择冲突项采用的译名，无差异项已默认采用。"
            elif review:
                next_action = "review"
                detail += "请处理与已有决定不同的译名，无差异项无需确认。"
            else:
                next_action = "publish"
                detail += "应用后用于项目翻译，也可以丢弃本次结果。"
        elif has_version:
            title = "项目术语已生效"
            next_action = "edit"
            detail = "可以继续提取或导入术语。查看和手动修改请打开“查看与编辑术语”。"
        else:
            title = "开始建立项目术语"
            next_action = "build"
            detail = "提取或导入术语 → 只处理有差异的译名 → 应用结果。"
        return cls(
            title,
            detail,
            has_draft,
            has_version,
            review,
            conflicts,
            has_draft and complete and current and not (review + conflicts),
            next_action,
        )
