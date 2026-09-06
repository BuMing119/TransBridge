"""Source-aware targeting shared by task estimates, preflight and execution."""

from __future__ import annotations

from dataclasses import dataclass

from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.application.translation.ai_execution_profile import AiExecutionProfile

from .scope_presenter import ScopePresenter


@dataclass(frozen=True, slots=True)
class SourceTask:
    key: str
    label: str
    esp_path: str | None
    collection: object
    translate_entries: tuple
    polish_entries: tuple

    @property
    def entries(self) -> tuple:
        return self.translate_entries + self.polish_entries


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

    def build(self, slots, state, *, mode: str, config, overwrite: bool) -> tuple[SourceTask, ...]:
        from transbridge.paratranz.config_manager import apply_rules

        profile = AiExecutionProfile.from_config(mode, config)
        by_object = {id(slot): str(key) for key, slot in self._ctx.slots.items()}
        tasks = []
        for slot in slots:
            if id(slot) not in by_object:
                raise ValueError("处理来源已变化，请重新打开 AI 翻译任务。")
            # Selection IDs may repeat across plugins. Never apply table shortcuts to another source.
            if state.preset in {"selection", "table_view"} and slot is not self._active:
                candidates = []
            else:
                presenter = ScopePresenter(
                    collection_provider=lambda slot=slot: slot.collection,
                    label_projection_provider=lambda: self._ctx.entry_labels,
                    category_of=self._category_of,
                    workbench=self._table if slot is self._active else SourceWorkbenchScope(),
                )
                presenter.restore(state)
                candidates = presenter.candidates()
            candidates = [
                entry
                for entry in candidates
                if DEFAULT_STAGE_POLICY.allows_ai(entry.stage, entry.translation, original=entry.original)
            ]
            translate, polish = [], []
            if mode == "mixed":
                actions = apply_rules(config.action_rules, candidates)
                translate = [
                    e for e in candidates if actions.get(e.id) == "translate" and (not e.translation or e.stage == 0)
                ]
                polish = [e for e in candidates if actions.get(e.id) == "polish" and e.translation]
            elif mode == "polish":
                polish = [e for e in candidates if e.translation]
            else:
                translate = [e for e in candidates if overwrite or not e.translation or e.stage == 0]
            if not profile.has_proofread_work:
                polish = []
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


def estimate_tasks(tasks, config) -> str:
    from transbridge.ai_translator.batch_planner import BatchPlanner

    total = sum(len(task.collection) for task in tasks)
    eligible = sum(
        DEFAULT_STAGE_POLICY.allows_ai(e.stage, e.translation, original=e.original)
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
