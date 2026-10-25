"""Bind explicit recovery records to the current project's exact entry identities."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from enum import Enum
from hashlib import sha256
import json
import logging

from PyQt6.QtCore import QObject, pyqtSignal

from transbridge.application.translation.task_recovery import TaskRecoveryStore

from .task_recovery_persistence import RecoveryInitialization, RecoverySource, RecoveryUpdate
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
    if isinstance(value, (set, frozenset)):
        return [_signature_value(item) for item in sorted(value, key=str)]
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

    # Discovery already owns detached JSON. SelectionCapture copies this large
    # immutable-by-contract record in bounded GUI slices before background use.
    window._recovery_selection = (record, bool(restart))
    view = window._view
    controls = view.controls
    mode = record["mode"]
    if mode not in {"translate", "polish", "mixed", "custom"}:
        raise ValueError("原任务模式无法恢复，请新建任务。")
    getattr(controls, f"mode_{mode}").setChecked(True)
    if not restart and not record.get("legacy") and "execution_config" in record:
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
        + (
            "重新开始：使用当前配置，忽略原任务候选。"
            if restart
            else "恢复旧任务：使用当前配置，核验已保存译文后接续处理并重新校对。"
            if record.get("legacy")
            else "继续任务：核验已保存进度，处理剩余词条。"
        )
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

    handed_off = False
    registry = getattr(window, "_task_recovery_registry", None)
    if registry is not None and hasattr(registry, "release_recovery_request"):
        registry.release_recovery_request(record["task_id"])
    try:
        configure_recovery(window, record, restart=restart)
        if restart or record.get("legacy"):
            # Scope evaluation and empty-task completion belong to the async
            # launch after the user starts this newly configured attempt.
            show_and_activate(window)
        else:
            window.on_start()
        # A continuation now starts asynchronously. TaskLaunch owns its window
        # until successful publication; closing here would cancel preparation.
        handed_off = True
    except Exception as exc:
        logging.getLogger(__name__).exception("AI task recovery could not open")
        QMessageBox.warning(window, "AI 任务未启动", str(exc))
    finally:
        if not handed_off:
            window.close()


def recovery_sources(window, config) -> tuple[SourceTask, ...]:
    from .task_source_selection import recover_task_sources

    selection = getattr(window, "_recovery_selection", None)
    if selection is None:
        return tuple(window._task_sources(config=config))
    slots = {str(key): slot for key, slot in window._ctx.slots.items()}
    return recover_task_sources(selection, window._ctx.active_version_identity, window._view_port.mode, config, slots)


def complete_empty_recovery(window, tasks) -> bool:
    """Close a validated recovery with no remaining work without starting an AI run."""
    from .task_empty_recovery import EmptyRecoveryCompletion, publish_empty_recovery

    selection = getattr(window, "_recovery_selection", None)
    if selection is None or any(task.entries for task in tasks):
        return False
    provider = getattr(window, "_task_project_directory_provider", None)
    directory = (
        provider()
        if provider is not None
        else getattr(getattr(window._ctx, "active_project", None), "project_dir", None)
    )
    EmptyRecoveryCompletion(directory, selection).execute()
    publish_empty_recovery(window)
    return True


class TaskRecoveryBinding(QObject):
    """Capture GUI-owned values, then serialize recovery work without live state.

    ``asynchronous=True`` is the task-window path. The synchronous default keeps
    non-Qt consumers compatible; both modes execute the same detached operations.
    """

    changed = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, request, session, parent=None, *, asynchronous=False):
        super().__init__(parent)
        self.session = session
        self.asynchronous = asynchronous
        self.store = None
        self.record = None
        self.worker = None
        self.pending = None
        self.error = ""
        self._delivery = None
        self._initialized = False
        self._initialization_failed = False
        self._sequence = 0
        self._saved_entries = None
        self._prepare_callbacks = None
        self.supported = bool(
            session.project_dir is not None
            and hasattr(request, "recovery_task_id")
            and any(task.entries for task in session.tasks)
        )
        if not self.supported:
            return
        self.store = TaskRecoveryStore(session.project_dir / "ai-task-recovery")
        reference = getattr(getattr(request, "terminology_binding", None), "snapshot_ref", None)
        self._initialization = RecoveryInitialization(
            request.run_id,
            request.recovery_task_id,
            tuple(map(str, session.version_identity)),
            request.spec.mode,
            deepcopy(request.config),
            tuple(
                RecoverySource(
                    task.key,
                    task.label,
                    tuple(entry.identity for entry in task.translate_entries),
                    tuple(entry.identity for entry in task.polish_entries),
                    task.entries,
                    str(task.esp_path) if task.esp_path is not None else None,
                )
                for task in session.tasks
            ),
            overwrite=bool(getattr(request.spec, "overwrite", False)),
            origin=getattr(request, "recovery_record", None),
            reference_snapshot=None if reference is None else reference.to_dict(),
        )
        self._task_id = request.recovery_task_id or request.run_id
        if not asynchronous:
            self.record = self._initialization.execute(self.store)
            self._initialized = True

    @property
    def busy(self):
        return self.worker is not None or self.pending is not None

    def release(self):
        """Release the task lease only after all durable writes have settled."""
        if self.busy:
            raise RuntimeError("恢复记录仍在保存，不能释放任务锁。")
        if self.store is not None:
            self.store.release()

    def prepare(self, *, on_success, on_error):
        """Validate/create the manifest before snapshots or model work may start."""
        if not self.supported or self._initialized:
            on_success(self.record)
            return
        if self.worker is not None:
            raise RuntimeError("恢复记录正在初始化")
        self._prepare_callbacks = on_success, on_error
        self._initialization_failed = False
        operation, store = self._initialization, self.store
        self._start(lambda: operation.execute(store), initialization=True)

    def update(self, run, *, frozen_entry_results=None):
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
        entries = frozen_entry_results
        if entries is None:
            # Real ledgers contain frozen rows and private, never-mutated before
            # snapshots. Only the small compatibility path needs a deep copy.
            freeze = getattr(run.entries, "freeze", None)
            entries = freeze() if callable(freeze) else deepcopy(run.entries)
        self._sequence += 1
        if self.session.project_saved:
            self._saved_entries = entries
        update = RecoveryUpdate(self._sequence, state, bool(self.session.project_saved), entries, self._saved_entries)
        if not self.asynchronous:
            self.record = update.execute(self.store, self._task_id)
            return
        if self._initialization_failed:
            return  # The initialization error was already delivered to the owner.
        self.pending = update
        if self._initialized and self.worker is None:
            self._start_pending()

    def _start_pending(self):
        if self.pending is None or self.worker is not None:
            return
        update, self.pending = self.pending, None
        store, task_id = self.store, self._task_id
        self._start(lambda: update.execute(store, task_id), sequence=update.sequence)

    def _start(self, operation, *, initialization=False, sequence=0):
        from transbridge.ui.workers import ApiWorker

        from .task_apply_preparation import ApplyDelivery

        self.error = ""
        worker = ApiWorker(operation, route_http_errors=False)
        self.worker = worker
        self._delivery = ApplyDelivery(
            worker,
            lambda value, error: self._delivered(value, error, initialization=initialization, sequence=sequence),
        )
        try:
            worker.start()
        except Exception as exc:
            self._delivery.deleteLater()
            worker.deleteLater()
            self._delivered(None, str(exc), initialization=initialization, sequence=sequence)
            return
        self.changed.emit()

    def _delivered(self, value, error, *, initialization, sequence):
        self.worker = self._delivery = None
        if initialization:
            callbacks, self._prepare_callbacks = self._prepare_callbacks, None
            if error is not None:
                self._initialization_failed = True
                self.pending = None
            else:
                self._initialized = True
                self.record = value
        else:
            callbacks = None
            if error is None and sequence == self._sequence:
                self.record = value
        if error is not None:
            self.error = str(error)
            self.failed.emit(self.error)
        if callbacks is not None:
            callbacks[1](str(error)) if error is not None else callbacks[0](value)
        self._start_pending()
        self.changed.emit()


__all__ = ["TaskRecoveryBinding", "complete_empty_recovery", "configure_recovery", "recovery_sources", "signature"]
