# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Source-aware targeting shared by task estimates, preflight and execution."""

from __future__ import annotations

from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.application.translation.task_scope import SourceTask, task_entry_groups
from transbridge.application.workbench_filters import FilterState, filtered_rows
from transbridge.ui.source_input_state import original_known

from .scope_presenter import ScopePresenter


class SourceWorkbenchScope:
    """Table shortcuts belong only to the source captured when the task opened."""

    def __init__(self, entries=(), selected=()) -> None:
        self._entries = tuple(entries)
        self._selected = tuple(selected)

    def filtered_entries(self) -> tuple:
        return self._entries

    def selected_entry_ids(self) -> tuple:
        return self._selected

    def locate_entry(self, entry_id: str) -> None:
        return None


class TaskScope:
    def __init__(self, ctx, workbench, category_of) -> None:
        self._ctx = ctx
        self._category_of = category_of
        self._active = getattr(ctx, "active_slot", None)
        self._table = SourceWorkbenchScope(workbench.filtered_entries(), workbench.selected_entry_ids())

    def build(
        self, slots, state, *, mode: str, config, overwrite: bool, missing_originals=None
    ) -> tuple[SourceTask, ...]:
        by_object = {id(slot): str(key) for key, slot in self._ctx.slots.items()}
        tasks = []
        for slot in slots:
            if id(slot) not in by_object:
                raise ValueError("处理来源已变化，请重新打开 AI 翻译任务。")
            candidates = self.candidates(slot, state)
            if missing_originals is not None and any(not original_known(entry) for entry in candidates):
                missing_originals.add(by_object[id(slot)])
            memberships = None
            if mode == "mixed" and any(rule.label_filter for rule in config.action_rules):
                rows = filtered_rows(slot.collection, FilterState(), self._ctx.entry_labels)
                memberships = {entry.identity: labels for entry, _, labels in rows}
            translate, polish = task_entry_groups(
                candidates, mode=mode, config=config, overwrite=overwrite, memberships=memberships
            )
            tasks.append(
                SourceTask(
                    by_object[id(slot)],
                    str(slot.label or by_object[id(slot)]),
                    slot.esp_path,
                    slot.collection,
                    tuple(translate),
                    tuple(polish),
                )
            )
        return tuple(tasks)

    def candidates(self, slot, state):
        """Resolve selected rows before source-text eligibility removes unknown originals."""
        if state.preset in {"selection", "table_view"} and slot is not self._active:
            return []
        presenter = ScopePresenter(
            collection_provider=lambda: slot.collection,
            label_projection_provider=lambda: self._ctx.entry_labels,
            category_of=self._category_of,
            workbench=self._table if slot is self._active else SourceWorkbenchScope(),
        )
        presenter.restore(state)
        return presenter.candidates()


def estimate_tasks(tasks, config) -> str:
    from transbridge.ai_translator.batch_planner import BatchPlanner

    total = sum(len(task.collection) for task in tasks)
    eligible = sum(
        bool(e.original.strip()) and DEFAULT_STAGE_POLICY.allows_ai(e.stage, e.translation, original=e.original)
        for task in tasks
        for e in task.collection
    )
    count = sum(len(task.entries) for task in tasks)
    requests = oversized = 0
    for task in tasks:
        plan = BatchPlanner(max_tokens_per_batch=config.max_tokens_per_batch, model=config.model).plan(
            list(task.translate_entries)
        )
        requests += len(plan.all_batches())
        oversized += len(plan.oversized)
    text = f"已选 {len(tasks)} 个插件 · 总条目 {total:,} · 可处理 {eligible:,} · 本次任务 {count:,} 条"
    text += f"\n预计翻译请求 {requests} 个（不含校对、术语及重试） · 共享并发 {config.max_concurrent}"
    if oversized:
        text += f" · 超限 {oversized} 条"
    return text
