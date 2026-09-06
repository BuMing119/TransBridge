"""Immutable, task-language projections for the terminology workbench."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TerminologyArea(StrEnum):
    OVERVIEW = "overview"
    TERMS = "terms"
    SCHEMES = "schemes"
    VERSIONS = "versions"
    REPORTS = "reports"


TERMINOLOGY_AREAS = (
    (TerminologyArea.OVERVIEW, "概览", "layout-dashboard"),
    (TerminologyArea.TERMS, "术语", "language"),
    (TerminologyArea.SCHEMES, "译名方案", "sparkles"),
    (TerminologyArea.VERSIONS, "版本", "clock-hour-3"),
    (TerminologyArea.REPORTS, "报告", "list-details"),
)


@dataclass(frozen=True, slots=True)
class TechnicalDetail:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class SourceScopeViewState:
    source_id: str
    name: str
    format_label: str
    technical_details: tuple[TechnicalDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class TerminologyPreflightViewState:
    ready: bool
    title: str
    message: str
    project_label: str = ""
    variant_label: str = ""
    scope_label: str = ""
    current_version_label: str = ""
    project_display_name: str = ""
    variant_display_name: str = ""
    current_version_value: str = ""
    expected_scale_label: str = "构建后显示准确规模"
    sources: tuple[SourceScopeViewState, ...] = ()
    diagnostic_code: str | None = None
    technical_details: tuple[TechnicalDetail, ...] = ()
    action_label: str = "创建术语库"

    @classmethod
    def unavailable(
        cls, message: str, *, diagnostic_code: str = "TERMINOLOGY_UI_UNAVAILABLE"
    ) -> TerminologyPreflightViewState:
        return cls(False, "暂时不能构建术语库", message, diagnostic_code=diagnostic_code)


@dataclass(frozen=True, slots=True)
class TerminologySummaryViewState:
    title: str
    result: str
    decisions: str
    impact: str
    next_action: str
    is_partial: bool = False
    is_stale: bool = False
    technical_details: tuple[TechnicalDetail, ...] = ()
    term_count: int | None = None
    attention_count: int | None = None


@dataclass(frozen=True, slots=True)
class TerminologyNotice:
    title: str
    message: str
    impact: str
    recovery: str
    retry_label: str | None = None
    technical_details: tuple[TechnicalDetail, ...] = ()


_PHASE_LABELS = {
    "capture": "检查工程和来源",
    "parse": "读取已登记来源",
    "assemble": "整理双语内容",
    "extract": "提取术语",
    "reduce": "归并并检查异译",
    "persist": "保存构建结果",
    "validate": "检查发布条件",
    "publish": "发布术语库新版",
    "render": "生成报告文件",
    "finalize": "完成最后检查",
}


def phase_label(value: str) -> str:
    """Translate stable runtime phases without exposing their wire values."""

    return _PHASE_LABELS.get(value, value or "准备中")


def business_diagnostic(code: str, message: str = "") -> TerminologyNotice:
    """Project technical outcomes into impact-first, recoverable user language."""

    normalized = code.upper()
    if normalized in {"TERMINOLOGY_PROJECT_REQUIRED", "PROJECT_REQUIRED"}:
        return TerminologyNotice("需要先打开工程", "术语库按工程管理。", "尚未开始任何构建。", "打开工程后重试。")
    if normalized in {"TERMINOLOGY_VARIANT_REQUIRED", "VARIANT_REQUIRED"}:
        return TerminologyNotice(
            "需要选择翻译版本", "术语库会绑定当前翻译版本。", "尚未开始任何构建。", "选择版本后重试。"
        )
    if normalized in {"TERMINOLOGY_SOURCE_REQUIRED", "SOURCE_REQUIRED"}:
        return TerminologyNotice(
            "没有可用来源",
            "当前工程没有已启用的术语构建来源。",
            "不会生成空术语库，也不会影响已有历史。",
            "登记并启用来源后重新检查。",
        )
    if "RELATION" in normalized:
        return TerminologyNotice(
            "来源关系需要确认",
            "部分来源不能确定应与哪个原文来源配对。",
            "构建尚未开始，已有术语库和历史保持不变。",
            "在工程来源设置中完成关联后重试。",
        )
    if "CURSOR_STALE" in normalized:
        return TerminologyNotice(
            "列表内容已经更新",
            "正在按最新结果重新载入首屏。",
            "旧页面不会与新结果混合。",
            "无需操作；如仍未显示，可点击刷新。",
        )
    if "STALE" in normalized:
        return TerminologyNotice(
            "构建结果已不是当前状态",
            "工程、来源或翻译版本在构建后发生了变化。",
            "该结果只能查看，不能发布为当前术语库。历史与已有版本保持不变。",
            "按当前工程状态重新构建。",
        )
    if "PARTIAL" in normalized:
        return TerminologyNotice(
            "只完成了部分来源",
            "可查看已完成部分并导出质量报告。",
            "默认不能发布为当前完整术语库，已有版本保持不变。",
            "修复未读取来源后重新构建。",
        )
    if "LOG" in normalized or "CHANGELOG" in normalized or "ARTIFACT" in normalized:
        return TerminologyNotice(
            "新版已发布，但更新日志生成失败",
            "术语库新版已经生效，失败仅影响更新日志文件。",
            "发布历史和新版内容均已保留。",
            "可从已保存的发布记录重新生成，无需重新发布。",
            retry_label="重试生成更新日志",
        )
    if "SUPPRESS" in normalized:
        return TerminologyNotice(
            "此术语将不再使用",
            "术语证据和人工调整历史仍会保留。",
            "后续翻译不再优先采用它。",
            "可在人工调整中重新启用。",
        )
    if "NO_EVIDENCE" in normalized:
        return TerminologyNotice(
            "当前项目中暂未找到使用位置",
            "人工决定仍被保留，便于后续来源恢复时继续使用。",
            "不会删除历史证据或发布记录。",
            "确认不再需要时可选择“不再使用”。",
        )
    return TerminologyNotice(
        "操作未完成",
        message or "术语工作台未能完成这项操作。",
        "已有术语库、草稿和历史不会因此被覆盖。",
        "展开技术详情并按提示重试。",
        technical_details=(TechnicalDetail("诊断代码", code),),
    )


__all__ = [
    "TERMINOLOGY_AREAS",
    "SourceScopeViewState",
    "TechnicalDetail",
    "TerminologyNotice",
    "TerminologyPreflightViewState",
    "TerminologySummaryViewState",
    "TerminologyArea",
    "business_diagnostic",
    "phase_label",
]
