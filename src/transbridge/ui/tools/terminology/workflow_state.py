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
    next_action: str = "build"

    def display_copy(self, pending=0):
        if pending:
            return (
                "正在保存草稿…",
                f"还有 {pending} 项修改正在保存。保存完成后才能检查发布；当前生效内容保持不变。",
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
        current = build is not None and build.freshness == BuildFreshness.CURRENT
        if has_draft:
            count = len(assets.draft.decisions)
            title = "草稿待审核" if review + conflicts else "草稿已整理，等待发布检查"
            detail = f"{count:,} 条草稿术语 · {review} 条待复核 · {conflicts} 组异译。"
            if not complete or not current:
                title = "需要重新整理来源"
                next_action = "build"
                detail += "下一步：重新整理来源；当前结果不完整或已过期，暂不能发布。"
            elif conflicts:
                next_action = "conflicts"
                detail += "下一步：处理异译，逐组决定采用的译名；完成审核后才能发布。"
            elif review:
                next_action = "review"
                detail += "下一步：复核术语，确认受来源变化影响的译名后才能发布。"
            else:
                next_action = "publish"
                detail += "下一步：检查影响并发布，确认逐条变化后才会生效。"
        elif has_version:
            title = "正在使用已发布术语"
            next_action = "edit"
            detail = "下一步：继续编辑会建立草稿。保存草稿不会改变当前生效内容，发布后用于后续翻译。"
        else:
            title = "开始建立项目术语"
            next_action = "build"
            detail = "下一步：从来源生成草稿，再检查译名并发布。生成和保存草稿都不会直接用于翻译。"
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
