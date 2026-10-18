"""Task-oriented copy derived from the existing authoritative asset snapshot."""

from dataclasses import dataclass

from transbridge.application.terminology.models import BuildCompleteness, BuildFreshness


@dataclass(frozen=True, slots=True)
class TerminologyWorkflowState:
    title: str
    detail: str
    has_draft: bool = False
    has_version: bool = False
    review_count: int = 0
    conflict_count: int = 0
    can_publish: bool = False

    @classmethod
    def from_assets(cls, assets):
        has_draft = assets.draft is not None
        has_version = assets.version_ref is not None
        counts = assets.review_counts
        review = 0 if counts is None else counts.review_required_terms
        conflicts = 0 if counts is None else counts.unresolved_conflicts
        build = assets.build
        complete = build is not None and build.completeness == BuildCompleteness.FULL
        current = build is not None and build.freshness == BuildFreshness.CURRENT
        if has_draft:
            count = len(assets.draft.decisions)
            title = "草稿待审核" if review + conflicts else "草稿已整理，等待发布检查"
            detail = f"{count:,} 条草稿术语 · {review} 条待复核 · {conflicts} 组异译。修改发布后用于后续翻译。"
            if not complete or not current:
                title = "需要重新整理来源"
                detail = "来源整理结果不完整或已过期。请重新整理来源，完成审核后再检查发布。"
        elif has_version:
            title = "正在使用已发布术语"
            detail = "选择词条可编辑基础术语，或点击“继续编辑”建立草稿；保存草稿不会改变当前生效内容。"
        else:
            title = "开始建立项目术语"
            detail = "先从项目来源生成草稿，再检查译名并发布。译名方案用于切换同一组术语的不同译法。"
        return cls(
            title,
            detail,
            has_draft,
            has_version,
            review,
            conflicts,
            has_draft and complete and current and not (review + conflicts),
        )
