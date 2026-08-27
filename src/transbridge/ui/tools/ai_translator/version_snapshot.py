"""Asynchronous Project/Variant snapshot boundary for one AI workflow run."""

from __future__ import annotations

from collections.abc import Callable
from copy import copy
from datetime import datetime
from typing import TYPE_CHECKING

from transbridge.ui.version_persistence import VersionPersistence
from transbridge.ui.workers import ApiWorker

if TYPE_CHECKING:
    from transbridge.ui.context import AppContext


_MODE_LABELS = {
    "translate": "翻译",
    "polish": "润色",
    "mixed": "混合",
    "custom": "自定义",
}


class AiVersionSnapshotSession:
    """Own the before/save-after snapshot sequence for exactly one AI run."""

    def __init__(self, context: AppContext, run_spec: object, *, display_mode: str | None = None) -> None:
        identity = context.active_version_identity
        if identity is None:
            raise RuntimeError("请先打开一个项目版本，AI 工作流需要先创建版本快照。")
        self._context = context
        self._identity = identity
        self._persistence = VersionPersistence(context, identity)
        self._worker: ApiWorker | None = None
        self._completed = False
        self._saved = False
        mode = display_mode or str(getattr(run_spec, "mode", "translate"))
        mode_label = _MODE_LABELS.get(mode, mode)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = str(getattr(run_spec, "run_id", "run"))[-8:]
        self.before_snapshot_name = f"AI-{mode_label}-执行前-{timestamp}-{run_id}"
        self.after_snapshot_name = f"AI-{mode_label}-保存后-{timestamp}-{run_id}"

    @property
    def is_busy(self) -> bool:
        return self._worker is not None

    @property
    def saved(self) -> bool:
        return self._saved

    @property
    def can_save(self) -> bool:
        return self._completed and not self._saved

    def mark_completed(self) -> None:
        self._completed = True

    def capture_before(
        self,
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        entries = self._freeze_entries()
        self._run(
            lambda: _require_success(self._persistence.create_snapshot(self.before_snapshot_name, entries)),
            on_success=on_success,
            on_error=on_error,
        )

    def save_translation(
        self,
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        if not self._completed:
            raise RuntimeError("AI 工作流尚未正常完成，不能保存翻译。")
        if self._saved:
            on_success({"snapshot_name": self.after_snapshot_name, "already_saved": True})
            return
        entries = self._freeze_entries()

        def saved(result: object) -> None:
            self._saved = True
            on_success(result)

        self._run(
            lambda: _require_success(self._persistence.save_translation(entries, self.after_snapshot_name)),
            on_success=saved,
            on_error=on_error,
        )

    def _freeze_entries(self) -> tuple[object, ...]:
        collection = self._context.collection
        if collection is None:
            raise RuntimeError("当前翻译集合已关闭，无法创建版本快照。")
        return tuple(copy(entry) for entry in collection)

    def _run(
        self,
        operation: Callable[[], object],
        *,
        on_success: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        if self._worker is not None:
            raise RuntimeError("版本快照操作正在进行，请稍候。")
        worker = ApiWorker(operation, route_http_errors=False)
        self._worker = worker
        worker.result.connect(on_success)
        worker.error.connect(on_error)

        def cleanup() -> None:
            if self._worker is worker:
                self._worker = None
            worker.deleteLater()

        worker.finished.connect(cleanup)
        worker.start()


def prepare_versioned_run(
    context: AppContext,
    run_spec: object,
    *,
    display_mode: str,
    on_ready: Callable[[AiVersionSnapshotSession], None],
    on_error: Callable[[str], None],
) -> AiVersionSnapshotSession | None:
    """Create the pre-run snapshot and continue only after it is durable."""

    try:
        session = AiVersionSnapshotSession(context, run_spec, display_mode=display_mode)
        session.capture_before(on_success=lambda _result: on_ready(session), on_error=on_error)
    except Exception as exc:
        on_error(str(exc))
        return None
    return session


def _require_success(result: object) -> object:
    if not hasattr(result, "is_success") or bool(getattr(result, "is_success")):
        return result
    diagnostics = tuple(getattr(result, "diagnostics", ()))
    if diagnostics:
        diagnostic = diagnostics[0]
        code = str(getattr(diagnostic, "code", "VERSION_OPERATION_FAILED"))
        message = str(getattr(diagnostic, "message", "版本操作失败。"))
        raise RuntimeError(f"{code}: {message}")
    raise RuntimeError("版本操作失败。")


__all__ = ["AiVersionSnapshotSession", "prepare_versioned_run"]
