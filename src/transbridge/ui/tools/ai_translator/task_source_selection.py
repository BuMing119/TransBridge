"""Capture scope intent on the GUI, then select from detached source snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.converter.translation_entry_collection import TranslationEntryCollection

from .task_scope import SourceTask, SourceWorkbenchScope, TaskScope


def _copy_json_steps(value):
    """Copy a recovery record without one unbounded deepcopy on the GUI thread."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key] = yield from _copy_json_steps(item)
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.append((yield from _copy_json_steps(item)))
        return tuple(result) if isinstance(value, tuple) else result
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise TypeError("恢复任务记录包含不支持的数据类型。")
    yield
    return value


@dataclass(frozen=True)
class SelectionIntent:
    identity: tuple
    sources: tuple  # (source key, label, esp path), already checked against GUI slots.
    selected_sources: tuple
    active_source: str | None
    scope: object
    config: object
    mode: str
    overwrite: bool
    table_entries: tuple  # (EntryKey, original object id), never live entry references.
    selected_ids: tuple
    labels: dict
    category_of: object
    recovery: tuple | None

    def select(self, states, origins):
        slots = {
            key: SimpleNamespace(label=label, esp_path=path, collection=TranslationEntryCollection(states[key].entries))
            for key, label, path in self.sources
        }
        if self.recovery is not None:
            return recover_task_sources(self.recovery, self.identity, self.mode, self.config, slots)
        table = []
        if self.scope.preset == "table_view" and self.active_source in self.selected_sources:
            active = slots.get(self.active_source)
            for key, token in self.table_entries:
                if active is None or origins.get(key) != token:
                    raise ValueError("AI 任务条目不属于其处理来源，请重新选择处理内容。")
                entry = active.collection.get(key)
                if entry is None:
                    raise ValueError("AI 任务条目不属于其处理来源，请重新选择处理内容。")
                table.append(entry)
        ctx = SimpleNamespace(slots=slots, active_slot=slots.get(self.active_source), entry_labels=self.labels)
        scope = TaskScope(ctx, SourceWorkbenchScope(tuple(table), self.selected_ids), self.category_of)
        return scope.build(
            tuple(slots[key] for key in self.selected_sources),
            self.scope,
            mode=self.mode,
            config=self.config,
            overwrite=self.overwrite,
        )


class SelectionCapture:
    """A GUI-owned capture plan; its finished SelectionIntent contains no UI references."""

    def __init__(self, window, config):
        ctx = window._ctx
        self._ctx = ctx
        slots = {str(key): slot for key, slot in ctx.slots.items()}
        by_object = {id(slot): key for key, slot in slots.items()}
        self.identity = ctx.active_version_identity
        self.sources = tuple((key, str(slot.label or key), slot.esp_path) for key, slot in slots.items())
        self._recovery = getattr(window, "_recovery_selection", None)
        self.mode, self.overwrite = window._view_port.mode, window._view_port.overwrite
        self.config = config  # The launcher already owns a detached configuration.
        self.scope = None if self._recovery else window._scope_presenter.state
        self.selected_sources, self.active_source = (), None
        self._table, self.selected_ids, self._labels = (), (), {}
        self.category_of = None
        if self._recovery is None:
            selected = window._view.sources_panel.selected_slots()
            if any(id(slot) not in by_object for slot in selected):
                raise ValueError("处理来源已变化，请重新打开 AI 翻译任务。")
            self.selected_sources = tuple(by_object[id(slot)] for slot in selected)
            source_scope = window._task_scope
            self.active_source = by_object.get(id(source_scope._active))
            self.category_of = source_scope._category_of
            if self.scope.preset == "table_view":
                self._table = source_scope._table.filtered_entries()
            self.selected_ids = source_scope._table.selected_entry_ids()
            if self.scope.label_filters:
                self._labels = ctx.entry_labels
        self.table, self.labels, self.recovery = [], {}, None
        self.intent = None

    def steps(self):
        for entry in self._table:
            self.table.append((entry.identity, id(entry)))
            yield
        for key, labels in self._labels.items():
            self.labels[key] = set(labels)
            yield
        if self._recovery is not None:
            self.recovery = yield from _copy_json_steps(self._recovery)
        self.intent = SelectionIntent(
            self.identity,
            self.sources,
            self.selected_sources,
            self.active_source,
            self.scope,
            self.config,
            self.mode,
            self.overwrite,
            tuple(self.table),
            self.selected_ids,
            self.labels,
            self.category_of,
            self.recovery,
        )

    def require_current(self):
        if self.scope is not None and self.scope.label_filters and self._ctx.entry_labels != self.labels:
            raise RuntimeError("条目标签在 AI 任务准备期间已修改，请重新运行任务。")
        if self._recovery is not None and self._recovery != self.recovery:
            raise RuntimeError("恢复任务记录在准备期间已修改，请重新运行任务。")


def capture_selection_intent(window, config):
    return SelectionCapture(window, config)


def recover_task_sources(selection, identity, mode, config, slots):
    """Shared recovery selection core; callers may provide live or frozen sources."""
    from .task_recovery_binding import signature

    record, restart = selection
    if identity is None or tuple(map(str, identity)) != (record["project_id"], record["variant_id"]):
        raise ValueError("恢复任务的项目或版本已变化，请重新打开原版本。")
    if mode != record["mode"]:
        raise ValueError("原任务模式已变化，请新建任务。")
    if not restart and not record.get("legacy") and signature(config) != record["config_digest"]:
        raise ValueError("AI 配置已变化，不能继续原任务；请选择重新开始。")
    applied = {}
    if not restart:
        for row in record.get("applied_entries", ()):
            key = EntryKey.from_dict(row["entry_key"])
            if key in applied:
                raise ValueError("恢复任务的已应用条目重复。")
            applied[key] = row
    seen_sources, seen_entries, tasks = set(), set(), []
    strict = not restart and (record.get("recovery_contract") == 2 or record.get("legacy"))
    results = {EntryKey.from_dict(row["entry_key"]): row for row in (record.get("results") or {}).get("rows", ())}
    for source in record["sources"]:
        source_key = source["key"]
        if source_key in seen_sources:
            raise ValueError(f"恢复任务来源重复：{source_key}")
        seen_sources.add(source_key)
        slot = slots.get(source_key)
        if strict and slot is None:
            raise ValueError(f"恢复任务的来源已不存在：{source['label']}。请重新打开原项目来源。")
        inputs = {EntryKey.from_dict(row["entry_key"]): row for row in source.get("inputs", ())}
        if strict and len(inputs) != len(source.get("inputs", ())):
            raise ValueError(f"恢复任务的输入快照重复：{source['label']}。")
        groups = []
        for field in ("translate_keys", "polish_keys"):
            entries = []
            for raw_key in source[field]:
                key = EntryKey.from_dict(raw_key)
                if key in seen_entries:
                    raise ValueError("恢复任务包含重复条目身份。")
                seen_entries.add(key)
                if slot is None:
                    continue
                entry = slot.collection.get(key)
                if entry is None:
                    if strict:
                        raise ValueError(f"恢复任务的词条已不存在：{source['label']} · {key.local_key}。")
                    continue
                saved = applied.get(key)
                if saved is not None and (
                    entry.original == saved["original"]
                    and entry.translation == saved["text"]
                    and entry.stage == saved["stage"]
                    and (not strict or (key in inputs and (entry.context or "") == inputs[key]["context"]))
                ):
                    continue
                if strict:
                    before = inputs.get(key)
                    if before is None:
                        raise ValueError(f"恢复任务缺少词条输入快照：{source['label']} · {key.local_key}。")
                    actual = (entry.original, entry.translation, entry.stage, entry.context or "")
                    expected = tuple(before[name] for name in ("original", "translation", "stage", "context"))
                    result = results.get(key)
                    matches_result = bool(
                        result
                        and result["source"] == source_key
                        and result["status"] == "succeeded"
                        and result["decision"] == "accepted"
                        and actual
                        == (
                            result["before"]["original"],
                            result["text"],
                            result["stage"],
                            result["before"]["context"],
                        )
                    )
                    if actual != expected and not matches_result:
                        raise ValueError(
                            f"恢复任务的词条已修改：{source['label']} · {key.local_key}。"
                            "已保存进度保持原样，请核对当前内容后重新开始。"
                        )
                if not entry.original.strip() or not DEFAULT_STAGE_POLICY.allows_ai(
                    entry.stage, entry.translation, original=entry.original
                ):
                    if strict:
                        raise ValueError(f"恢复任务的词条当前不可处理：{source['label']} · {key.local_key}。")
                    continue
                entries.append(entry)
            groups.append(tuple(entries))
        if slot is not None:
            tasks.append(SourceTask(source_key, slot.label, slot.esp_path, slot.collection, *groups))
    if not applied.keys() <= seen_entries:
        raise ValueError("恢复记录的已应用条目不属于原任务范围。")
    return tuple(tasks)
