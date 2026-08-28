"""Qt-free Workbench hierarchy, summary and contextual-action projections."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath

from transbridge.application.projects.source_registry import select_workbench_source
from transbridge.converter.translation_entry import (
    STAGE_CHECKED,
    STAGE_HIDDEN,
    STAGE_LABELS,
    STAGE_LOCKED,
    STAGE_QUESTIONABLE,
    STAGE_REVIEWED,
    STAGE_TRANSLATED,
    STAGE_UNTRANSLATED,
    TranslationEntry,
)
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.workbench.filters_presenter import FilterState


class WorkbenchContentKind(StrEnum):
    PLUGIN = "plugin"
    TRANSLATION_FILE = "translation-file"
    LOCALIZED_STRINGS = "localized-strings"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class WorkbenchContextIdentity:
    project_id: str
    variant_id: str
    content_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.project_id, self.variant_id, self.content_id)):
            raise ValueError("Workbench context identity values must not be empty")


@dataclass(frozen=True, slots=True)
class WorkbenchHierarchyViewState:
    identity: WorkbenchContextIdentity | None
    project_name: str
    variant_name: str
    content_name: str
    content_kind: WorkbenchContentKind
    project_label: str
    variant_label: str
    content_label: str


@dataclass(frozen=True, slots=True)
class StatisticsMetric:
    key: str
    label: str
    value: int
    description: str


_COMPLETED_STAGE_SEQUENCE = (STAGE_TRANSLATED, STAGE_CHECKED, STAGE_REVIEWED, STAGE_LOCKED)
_COMPLETED_STAGES = frozenset(_COMPLETED_STAGE_SEQUENCE)
_SUMMARY_STAGE_FILTERS = {
    "untranslated": frozenset((STAGE_UNTRANSLATED,)),
    "review": frozenset((STAGE_QUESTIONABLE,)),
    "completed": _COMPLETED_STAGES,
    "hidden": frozenset((STAGE_HIDDEN,)),
    "total": frozenset(),
}


@dataclass(frozen=True, slots=True)
class StatisticsSummary:
    total: int
    untranslated: int
    needs_review: int
    completed: int
    hidden: int = 0

    def __post_init__(self) -> None:
        if min(self.total, self.untranslated, self.needs_review, self.completed, self.hidden) < 0:
            raise ValueError("Workbench statistics cannot be negative")
        if self.untranslated + self.needs_review + self.completed + self.hidden != self.total:
            raise ValueError("Workbench statistics must partition the collection")

    @classmethod
    def from_entries(cls, entries: Sequence[TranslationEntry]) -> StatisticsSummary:
        untranslated = needs_review = completed = hidden = 0
        for entry in entries:
            if entry.stage == STAGE_HIDDEN:
                hidden += 1
            elif entry.stage == STAGE_UNTRANSLATED:
                untranslated += 1
            elif entry.stage == STAGE_QUESTIONABLE:
                needs_review += 1
            elif entry.stage in _COMPLETED_STAGES:
                completed += 1
            else:
                # Unknown external stages remain visible and actionable instead of
                # disappearing from the partition. Canonical stages always take
                # their meaning from ``stage``, never from whether text is empty.
                untranslated += 1
        return cls(len(entries), untranslated, needs_review, completed, hidden)

    def metrics(self) -> tuple[StatisticsMetric, ...]:
        """Return summary cards using the canonical translation-stage vocabulary."""

        completed_labels = "、".join(STAGE_LABELS[stage] for stage in _COMPLETED_STAGE_SEQUENCE)
        return (
            StatisticsMetric("total", "全部", self.total, "全部翻译状态"),
            StatisticsMetric(
                "untranslated",
                STAGE_LABELS[STAGE_UNTRANSLATED],
                self.untranslated,
                f"翻译状态：{STAGE_LABELS[STAGE_UNTRANSLATED]}",
            ),
            StatisticsMetric(
                "review",
                STAGE_LABELS[STAGE_QUESTIONABLE],
                self.needs_review,
                f"翻译状态：{STAGE_LABELS[STAGE_QUESTIONABLE]}",
            ),
            StatisticsMetric(
                "completed",
                STAGE_LABELS[STAGE_TRANSLATED],
                self.completed,
                f"包括翻译状态：{completed_labels}",
            ),
        )

    def filter_state(self, key: str, current: FilterState) -> FilterState:
        try:
            stages = _SUMMARY_STAGE_FILTERS[key]
        except KeyError:
            raise KeyError(key) from None
        return FilterState(
            categories=current.categories,
            stages=stages,
            labels=current.labels,
            search_key=current.search_key,
            search_original=current.search_original,
            search_translation=current.search_translation,
            focus_labeled=current.focus_labeled,
        )


@dataclass(frozen=True, slots=True)
class ContextActionViewState:
    intent_id: IntentId
    label: str
    enabled: bool
    reason: str | None = None
    secondary: bool = False

    def __post_init__(self) -> None:
        if self.enabled and self.reason is not None:
            raise ValueError("enabled Workbench action cannot carry a disabled reason")
        if not self.enabled and not (self.reason and self.reason.strip()):
            raise ValueError("disabled Workbench action requires a reason")


class WorkbenchWorkflowPresenter:
    """Build Workbench presentation state without owning application commands."""

    _TRANSLATION_FORMATS = frozenset({
        "xml.eet",
        "binary.eet",
        "xml.xt",
        "json.paratranz",
        "json.dsd",
        "json.transbridge",
        "sst.ssu8",
        "sst.ssu9",
    })
    _STRINGS_FORMATS = frozenset({"strings.strings", "strings.dlstrings", "strings.ilstrings"})

    def hierarchy(
        self,
        *,
        project_id: str | None,
        project_name: str | None,
        variant_id: str | None,
        variant_name: str | None,
        sources: Sequence[Mapping[str, object]],
        active_content_id: str | None = None,
    ) -> WorkbenchHierarchyViewState:
        project_name = (project_name or "").strip()
        variant_name = (variant_name or "").strip()
        if not project_id or not project_name or not variant_id:
            return WorkbenchHierarchyViewState(
                None,
                project_name or "无工程",
                variant_name or "无翻译版本",
                "无翻译内容",
                WorkbenchContentKind.GENERIC,
                "本地工程 · 无",
                "翻译版本 · 无",
                "翻译内容 · 无",
            )

        source = self._select_source(sources, active_content_id)
        content_id = str(
            source.get("source_id") or source.get("namespace") or source.get("location") or active_content_id or "empty"
        )
        format_id = str(source.get("format_id") or "").lower()
        location = str(source.get("location") or source.get("path") or "")
        content_name = str(source.get("display_name") or self._basename(location) or "空翻译内容")
        kind = self.content_kind(format_id)
        kind_label = {
            WorkbenchContentKind.PLUGIN: "插件",
            WorkbenchContentKind.TRANSLATION_FILE: "翻译内容",
            WorkbenchContentKind.LOCALIZED_STRINGS: "本地化字符串",
            WorkbenchContentKind.GENERIC: "翻译内容",
        }[kind]
        identity = WorkbenchContextIdentity(str(project_id), str(variant_id), content_id)
        return WorkbenchHierarchyViewState(
            identity,
            project_name,
            variant_name or str(variant_id),
            content_name,
            kind,
            f"本地工程 · {project_name}",
            f"翻译版本 · {variant_name or variant_id}",
            f"{kind_label} · {content_name}",
        )

    @staticmethod
    def actions(
        *,
        has_context: bool,
        visible_entries: int,
        needs_review: int,
        write_supported: bool,
    ) -> tuple[ContextActionViewState, ...]:
        scope_ready = has_context and visible_entries > 0
        no_scope = "当前翻译内容没有可操作词条"
        return (
            ContextActionViewState(
                IntentId.TRANSLATION_AI,
                "AI 翻译",
                scope_ready,
                None if scope_ready else no_scope,
            ),
            ContextActionViewState(
                IntentId.TRANSLATION_REVIEW,
                "检查",
                has_context and needs_review > 0,
                None if has_context and needs_review > 0 else f"当前没有“{STAGE_LABELS[STAGE_QUESTIONABLE]}”状态的词条",
            ),
            ContextActionViewState(
                IntentId.PUBLISH_WRITE,
                "写回/发布",
                scope_ready and write_supported,
                None
                if scope_ready and write_supported
                else (no_scope if not scope_ready else "当前翻译内容不支持直接写回"),
            ),
            ContextActionViewState(
                IntentId.WORKBENCH_MANAGE,
                "更多",
                has_context,
                None if has_context else "请先打开工程",
                True,
            ),
        )

    @classmethod
    def supports_write(cls, kind: WorkbenchContentKind) -> bool:
        return kind is not WorkbenchContentKind.GENERIC

    @classmethod
    def content_kind(cls, format_id: str) -> WorkbenchContentKind:
        if format_id == "plugin.sse":
            return WorkbenchContentKind.PLUGIN
        if format_id in cls._STRINGS_FORMATS:
            return WorkbenchContentKind.LOCALIZED_STRINGS
        if format_id in cls._TRANSLATION_FORMATS:
            return WorkbenchContentKind.TRANSLATION_FILE
        return WorkbenchContentKind.GENERIC

    @staticmethod
    def _select_source(
        sources: Sequence[Mapping[str, object]],
        active_content_id: str | None,
    ) -> Mapping[str, object]:
        return select_workbench_source(sources, active_source_id=active_content_id)

    @staticmethod
    def _basename(value: str) -> str:
        if not value:
            return ""
        return PurePath(value.replace("\\", "/")).name


__all__ = [
    "ContextActionViewState",
    "StatisticsMetric",
    "StatisticsSummary",
    "WorkbenchContentKind",
    "WorkbenchContextIdentity",
    "WorkbenchHierarchyViewState",
    "WorkbenchWorkflowPresenter",
]
