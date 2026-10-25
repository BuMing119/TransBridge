"""Detached completion of a validated recovery that has no remaining entries."""

from dataclasses import dataclass

from transbridge.application.translation.task_recovery import TaskRecoveryStore


@dataclass(frozen=True)
class EmptyRecoveryCompletion:
    directory: object
    selection: tuple

    def execute(self):
        return self.prepare().commit()

    def prepare(self):
        from .task_recovery_binding import _STATES

        if self.directory is None:
            raise ValueError("恢复任务的项目目录不可用。")
        selected, restart = self.selection
        store = TaskRecoveryStore(self.directory / "ai-task-recovery")
        record = store.load(selected["task_id"])
        if any(
            record.get(key) != selected.get(key)
            for key in ("project_id", "variant_id", "mode", "config_digest", "sources")
        ):
            raise ValueError("恢复任务记录已变化，不能结束原任务。")
        if not record.get("supported", False) or record["state"] not in _STATES:
            raise ValueError("原任务恢复状态无效或不受支持。")
        if not restart and record["state"] in {"completed", "cancelled"}:
            raise ValueError("原任务已经完成或取消。")
        return PreparedEmptyRecovery(store, record)


@dataclass(frozen=True)
class PreparedEmptyRecovery:
    store: object
    record: dict

    def commit(self):
        task_id = self.record["task_id"]
        self.store.acquire(self.record.get("origin_task_id") or task_id)
        try:
            if self.store.load(task_id) != self.record:
                raise ValueError("恢复任务记录已变化，不能结束原任务。")
            return self.store.update(task_id, state="completed")
        finally:
            self.store.release()


def publish_empty_recovery(window):
    registry = getattr(window, "_task_recovery_registry", None)
    if registry is not None:
        for history in registry.history_windows:
            history.refresh()
        registry.changed.emit()
    window.close()
