"""Scope state and projection adapters for the AI translator."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Literal, Protocol, TypeVar

from transbridge.converter.translation_entry import STAGE_HIDDEN, STAGE_LOCKED

EntryT = TypeVar("EntryT")


class WorkbenchScopePort(Protocol[EntryT]):
    """Narrow read-only boundary supplied by the Workbench slice."""

    def filtered_entries(self) -> tuple[EntryT, ...]: ...

    def selected_entry_ids(self) -> tuple[str, ...]: ...

    def locate_entry(self, entry_id: str) -> None: ...


class Step2ScopeAdapter:
    """Adapter for S04's public filtered-entry and navigation contract."""

    def __init__(self, step2: object) -> None:
        self._step2 = step2

    def filtered_entries(self) -> tuple[object, ...]:
        return tuple(self._step2.filtered_entries())

    def selected_entry_ids(self) -> tuple[str, ...]:
        selector = getattr(self._step2, "selected_row_entry_ids", None)
        if not callable(selector):
            return ()
        return tuple(selector())

    def locate_entry(self, entry_id: str) -> None:
        locator = getattr(self._step2, "locate_entry", None)
        if callable(locator):
            locator(entry_id)


@dataclass(frozen=True, slots=True)
class TranslationScope:
    stage_filters: frozenset[int] = frozenset()
    label_filters: frozenset[str] = frozenset()
    category_filters: frozenset[str] = frozenset()
    preset: str | None = None
    selected_entry_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ScopeEstimate:
    target: Literal["standard", "mixed"]
    text: str


@dataclass(frozen=True, slots=True)
class MixedScope:
    translate_entries: tuple
    polish_entries: tuple


class ScopePresenter:
    """Owns translator scope state without owning widgets or domain data."""

    def __init__(
        self,
        collection_provider: Callable[[], Iterable[EntryT] | None],
        label_projection_provider: Callable[[], dict[str, set[str]]],
        category_of: Callable[[EntryT], str],
        workbench: WorkbenchScopePort[EntryT],
    ) -> None:
        self._collection_provider = collection_provider
        self._label_projection_provider = label_projection_provider
        self._category_of = category_of
        self._workbench = workbench
        self._state = TranslationScope()

    @property
    def state(self) -> TranslationScope:
        return self._state

    def reset_default(self, *, polish: bool) -> TranslationScope:
        stages = frozenset({1, 2, 3, 5} if polish else {0})
        self._state = TranslationScope(stage_filters=stages)
        return self._state

    def select_preset(self, preset: str) -> TranslationScope:
        if preset == "untranslated":
            self._state = TranslationScope(stage_filters=frozenset({0}))
        elif preset == "table_view":
            self._state = TranslationScope(preset="table_view")
        elif preset == "selection":
            self._state = TranslationScope(
                preset="selection",
                selected_entry_ids=frozenset(self._workbench.selected_entry_ids()),
            )
        return self._state

    def toggle_stage(self, stage: int | None) -> TranslationScope:
        values = set(self._state.stage_filters)
        self._toggle(values, stage)
        self._state = replace(self._state, stage_filters=frozenset(values), preset=None)
        return self._state

    def toggle_label(self, label_id: str | None) -> TranslationScope:
        values = set(self._state.label_filters)
        self._toggle(values, label_id)
        self._state = replace(self._state, label_filters=frozenset(values), preset=None)
        return self._state

    def toggle_category(self, category: str | None) -> TranslationScope:
        values = set(self._state.category_filters)
        self._toggle(values, category)
        self._state = replace(self._state, category_filters=frozenset(values), preset=None)
        return self._state

    def candidates(self) -> list[EntryT]:
        state = self._state
        if state.preset == "table_view":
            candidates = list(self._workbench.filtered_entries())
        else:
            collection = self._collection_provider()
            if collection is None:
                return []
            candidates = list(collection)
            if state.preset == "selection":
                candidates = [entry for entry in candidates if entry.id in state.selected_entry_ids]
            if state.stage_filters:
                candidates = [entry for entry in candidates if entry.stage in state.stage_filters]
            if state.label_filters:
                labels = self._label_projection_provider()
                candidates = [
                    entry for entry in candidates if entry.id and labels.get(entry.id, set()) & state.label_filters
                ]
            if state.category_filters:
                candidates = [entry for entry in candidates if self._category_of(entry) in state.category_filters]
        return [entry for entry in candidates if entry.stage not in (STAGE_LOCKED, STAGE_HIDDEN)]

    def locate_entry(self, entry_id: str) -> None:
        self._workbench.locate_entry(entry_id)

    def partition_mixed(self, rules: list, entries: Iterable[EntryT]) -> MixedScope:
        """Apply mixed-mode rules and return immutable execution inputs."""
        from transbridge.paratranz.config_manager import apply_rules

        snapshot = list(entries)
        actions = apply_rules(rules, snapshot)
        return MixedScope(
            tuple(entry for entry in snapshot if actions.get(entry.id) == "translate"),
            tuple(entry for entry in snapshot if actions.get(entry.id) == "polish"),
        )

    def estimate(
        self,
        *,
        mode: str,
        rules: list | None,
        overwrite: bool,
        max_tokens: int,
        model: str = "",
        max_concurrent: int = 1,
    ) -> ScopeEstimate:
        collection = self._collection_provider()
        if collection is None:
            return ScopeEstimate("mixed" if mode == "mixed" else "standard", "预计：— 条（需先加载集合）")
        if mode == "mixed":
            from transbridge.paratranz.config_manager import apply_rules

            actions = apply_rules(rules or [], list(collection))
            translate_count = sum(action == "translate" for action in actions.values())
            polish_count = sum(action == "polish" for action in actions.values())
            suffix = "（两者均为0，请调整规则）" if not translate_count and not polish_count else ""
            return ScopeEstimate(
                "mixed",
                f"预计：翻译 {translate_count} 条 + 润色 {polish_count} 条；全流程共享并发 {max_concurrent}{suffix}",
            )
        candidates = self.candidates()
        if mode == "polish":
            return ScopeEstimate(
                "standard",
                f"润色范围：{len(candidates)} 条已翻译词条；全流程共享并发 {max_concurrent}",
            )
        if not overwrite:
            candidates = [entry for entry in candidates if not entry.translation or entry.stage == 0]
        if not candidates:
            return ScopeEstimate("standard", "预计：0 条（无匹配条目，请调整作用域）")
        from transbridge.ai_translator.batch_planner import BatchPlanner

        plan = BatchPlanner(max_tokens_per_batch=max_tokens, model=model).plan(candidates)
        batches = plan.all_batches()
        token_counts = [batch.content_tokens for batch in batches]
        average_tokens = sum(token_counts) // len(token_counts) if token_counts else 0
        max_batch_tokens = max(token_counts, default=0)
        oversized = f"；超限 {len(plan.oversized)} 条" if plan.oversized else ""
        return ScopeEstimate(
            "standard",
            f"预计：{len(candidates)} 条 / {len(batches)} 个请求；"
            f"内容 Token 平均 {average_tokens}、最大 {max_batch_tokens}；"
            f"共享并发 {max_concurrent}{oversized}"
            f"（第一轮: {sum(len(batch.entries) for batch in plan.round1)}"
            f"  第二轮: {sum(len(batch.entries) for batch in plan.round2)}"
            f"  第三轮: {sum(len(batch.entries) for batch in plan.round3)}）",
        )

    @staticmethod
    def _toggle(values: set, value: object | None) -> None:
        if value is None:
            values.clear()
        elif value in values:
            values.discard(value)
        else:
            values.add(value)
