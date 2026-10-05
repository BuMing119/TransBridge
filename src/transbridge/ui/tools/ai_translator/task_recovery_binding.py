"""Bind explicit recovery records to the current project's exact entry identities."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
import logging

from transbridge.application.io.identity import EntryKey
from transbridge.application.io.stage_policy import DEFAULT_STAGE_POLICY
from transbridge.application.translation.proofread_checkpoint import proofread_settings
from transbridge.application.translation.task_recovery import TaskRecoveryStore

from .task_config_snapshot import execution_snapshot
from .task_scope import SourceTask

_OMITTED = frozenset({
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "credential_ref",
    "credentials",
    "max_concurrent",
    "llm_max_retries",
    "config_revision",
})
_STATES = frozenset({
    "preparing",
    "running",
    "applying",
    "pending_confirmation",
    "partial",
    "failed",
    "completed",
    "cancelled",
    "cancelling",
    "interrupted",
    "error",
})


def _signature_value(value):
    if is_dataclass(value):
        value = {field.name: getattr(value, field.name) for field in fields(value)}
    elif hasattr(value, "__dict__") and not isinstance(value, Enum):
        value = vars(value)
    if isinstance(value, Mapping):
        return {
            str(key): _signature_value(item)
            for key, item in value.items()
            if not str(key).startswith(("_", "assistant_", "mcp_"))
            and str(key).lower() not in _OMITTED
            and not any(word in str(key).lower() for word in ("credential", "password", "secret", "api_key"))
        }
    if isinstance(value, (tuple, list)):
        return [_signature_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"无法为恢复任务计算配置签名：{type(value).__name__}")


def signature(config) -> str:
    """Hash execution settings without credentials, retaining numeric token limits."""
    payload = json.dumps(_signature_value(config), ensure_ascii=False, sort_keys=True, allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def configure_recovery(window, record, restart=False):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QLabel

    window._recovery_selection = (deepcopy(record), bool(restart))
    view = window._view
    controls = view.controls
    mode = record["mode"]
    if mode not in {"translate", "polish", "mixed", "custom"}:
        raise ValueError("原任务模式无法恢复，请新建任务。")
    getattr(controls, f"mode_{mode}").setChecked(True)
    if not restart and "execution_config" in record:
        restored = window._config_presenter.restore_task_config(record["execution_config"], mode)
        if signature(restored) != record["config_digest"]:
            raise ValueError("原任务配置快照与签名不匹配，请重新开始。")
    for name in ("translate", "polish", "mixed", "custom"):
        getattr(controls, f"mode_{name}").setEnabled(False)
    for control in (view.sources_panel, view._scope_filter_box, controls.scope_stack):
        control.setEnabled(False)
    panel = view.sources_panel
    source_keys = {source["key"] for source in record["sources"]}
    panel.list.blockSignals(True)
    try:
        for index in range(panel.list.count()):
            item = panel.list.item(index)
            checked = str(item.data(Qt.ItemDataRole.UserRole)) in source_keys
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    finally:
        panel.list.blockSignals(False)
    total = sum(len(source["translate_keys"]) + len(source["polish_keys"]) for source in record["sources"])
    text = (
        f"原任务：{record['created_at']}\n"
        f"处理内容：{'、'.join(source['label'] for source in record['sources'])} · 原范围 {total} 条\n"
        + ("重新开始：使用当前配置，忽略原任务候选。" if restart else "继续任务：保留原范围，重新核验已保存结果。")
    )
    label = getattr(view, "recovery_notice", None)
    if label is None:
        label = QLabel(window)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        window.layout().insertWidget(1, label)
        view.recovery_notice = label
    label.setText(text)
    controls.start_btn.setText("重新开始" if restart else "继续任务")


def open_recovery(window, record, *, restart=False):
    """Continue immediately; only a fresh run needs the configuration screen."""
    from PyQt6.QtWidgets import QMessageBox

    from transbridge.ui.windowing import show_and_activate

    configured = False
    try:
        configure_recovery(window, record, restart=restart)
        configured = True
        if restart:
            show_and_activate(window)
        else:
            window.on_start()
    except Exception as exc:
        logging.getLogger(__name__).exception("AI task recovery could not open")
        QMessageBox.warning(window, "AI 任务未启动", str(exc))
    finally:
        if not restart or not configured:
            window.close()


def recovery_sources(window, config) -> tuple[SourceTask, ...]:
    selection = getattr(window, "_recovery_selection", None)
    if selection is None:
        return tuple(window._task_sources(config=config))
    record, restart = selection
    identity = window._ctx.active_version_identity
    if identity is None or tuple(map(str, identity)) != (record["project_id"], record["variant_id"]):
        raise ValueError("恢复任务的项目或版本已变化，请重新打开原版本。")
    if window._view_port.mode != record["mode"]:
        raise ValueError("原任务模式已变化，请新建任务。")
    if not restart and signature(config) != record["config_digest"]:
        raise ValueError("AI 配置已变化，不能继续原任务；请选择重新开始。")
    applied = {}
    if not restart:
        for row in record.get("applied_entries", ()):
            key = EntryKey.from_dict(row["entry_key"])
            if key in applied:
                raise ValueError("恢复任务的已应用条目重复。")
            applied[key] = row
    slots = {str(key): slot for key, slot in window._ctx.slots.items()}
    seen_sources, seen_entries, tasks = set(), set(), []
    for source in record["sources"]:
        source_key = source["key"]
        if source_key in seen_sources or source_key not in slots:
            raise ValueError(f"恢复任务来源缺失或重复：{source_key}")
        seen_sources.add(source_key)
        slot = slots[source_key]
        groups = []
        for field in ("translate_keys", "polish_keys"):
            entries = []
            for raw_key in source[field]:
                key = EntryKey.from_dict(raw_key)
                if key in seen_entries:
                    raise ValueError("恢复任务包含重复条目身份。")
                seen_entries.add(key)
                entry = slot.collection.get(key)
                if entry is None:
                    raise ValueError(f"原任务条目已缺失：{source_key} / {key.local_key}")
                if not entry.original.strip() or not DEFAULT_STAGE_POLICY.allows_ai(
                    entry.stage, entry.translation, original=entry.original
                ):
                    raise ValueError(f"原任务条目已锁定或无法处理：{source_key} / {key.local_key}")
                saved = applied.get(key)
                if saved is not None and (
                    entry.original == saved["original"]
                    and entry.translation == saved["text"]
                    and entry.stage == saved["stage"]
                ):
                    continue
                entries.append(entry)
            groups.append(tuple(entries))
        tasks.append(SourceTask(source_key, slot.label, slot.esp_path, slot.collection, *groups))
    if not applied.keys() <= seen_entries:
        raise ValueError("恢复记录的已应用条目不属于原任务范围。")
    return tuple(tasks)


class TaskRecoveryBinding:
    def __init__(self, request, session):
        self.session = session
        self.store = None
        self.record = None
        profile = request.spec.execution_profile
        self.supported = bool(
            session.project_dir is not None
            and hasattr(request, "recovery_task_id")
            and profile.enable_proofread
            and any(task.polish_entries for task in session.tasks)
            and not any(task.translate_entries for task in session.tasks)
        )
        if not self.supported:
            return
        self.store = TaskRecoveryStore(session.project_dir / "ai-task-recovery")
        continuing = request.recovery_task_id
        identity = tuple(map(str, session.version_identity))
        sources = [
            {
                "key": task.key,
                "label": task.label,
                "translate_keys": [entry.identity.to_dict() for entry in task.translate_entries],
                "polish_keys": [entry.identity.to_dict() for entry in task.polish_entries],
            }
            for task in session.tasks
        ]
        expected = dict(
            project_id=identity[0],
            variant_id=identity[1],
            mode=request.spec.mode,
            config_digest=signature(request.config),
        )
        if continuing:
            record = self.store.load(continuing)
            if record["state"] not in _STATES or not record.get("supported", False):
                raise ValueError("原任务恢复状态无效或不受支持。")
            if any(record.get(key) != value for key, value in expected.items()):
                raise ValueError("恢复任务身份或配置不匹配，不能覆盖原任务记录。")
            if record["state"] in {"completed", "cancelled"}:
                raise ValueError("原任务已经完成或取消，请重新开始。")
            known = {source["key"]: source for source in record["sources"]}
            for source in sources:
                old = known.get(source["key"])
                if old is None or any(
                    not {EntryKey.from_dict(key) for key in source[field]}
                    <= {EntryKey.from_dict(key) for key in old[field]}
                    for field in ("translate_keys", "polish_keys")
                ):
                    raise ValueError("恢复任务范围与原记录不匹配。")
            self.record = record
        else:
            self.record = self.store.create({
                "task_id": request.run_id,
                "created_at": datetime.now().astimezone().isoformat(),
                **expected,
                "checkpoint_settings": proofread_settings(request.config),
                "execution_config": execution_snapshot(request.config),
                "sources": sources,
                "state": "preparing",
                "supported": True,
            })

    def update(self, run):
        if self.store is None:
            return
        state = run.state
        if state not in _STATES:
            raise ValueError(f"无法记录未知任务状态：{state}")
        if getattr(run, "user_cancelled", False):
            state = "cancelled"
        elif run.shutting_down and not (self.session.completed and self.session.project_saved):
            state = "interrupted"
        elif state == "completed" and not self.session.project_saved:
            state = "applying"
        applied = {EntryKey.from_dict(row["entry_key"]): row for row in self.record.get("applied_entries", ())}
        if self.session.project_saved:
            for key in run.entries.applied_keys:
                row = run.entries.entries[key]
                applied[key] = {
                    "entry_key": key.to_dict(),
                    "original": row.before.original,
                    "text": row.text,
                    "stage": row.stage,
                }
        self.record = self.store.update(
            self.record["task_id"],
            state=state,
            applied_entries=list(applied.values()),
            applied=bool(applied or run.entries.applied_keys),
            project_saved=bool(self.session.project_saved),
        )


__all__ = ["TaskRecoveryBinding", "configure_recovery", "recovery_sources", "signature"]
