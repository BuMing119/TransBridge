"""Read the same effective terminology as AI runs without blocking the Qt event loop."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from PyQt6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal, pyqtSlot

from transbridge.ai_translator.project_terminology_runtime import freeze_project_terminology
from transbridge.application.terminology.effective import EffectiveSnapshotStatus

logger = logging.getLogger(__name__)


class _ReadSignals(QObject):
    finished = pyqtSignal(int, object, str, str)


class _ReadSummary(QRunnable):
    def __init__(self, generation, identity, factory, signals) -> None:
        super().__init__()
        self._generation = generation
        self._identity = identity
        self._factory = factory
        self._signals = signals

    def run(self) -> None:
        try:
            owner = SimpleNamespace(active_version_identity=self._identity, effective_terminology_factory=self._factory)
            snapshot = freeze_project_terminology(owner).run_snapshot
            if snapshot is None:
                raise RuntimeError("当前工程的术语服务未提供有效快照")
            if snapshot.ref.status is EffectiveSnapshotStatus.NO_PROJECT_VERSION:
                status = "尚未发布项目术语库。可在术语工作台构建并发布；当前任务仍使用下方术语来源。"
                version = ""
            else:
                count = sum(item.is_effective for item in snapshot.decisions)
                status = f"已发布 · 有效术语 {count} 条（当前版本总数）"
                if count == 0:
                    status += "。当前版本没有可用条目。"
                version = f"术语版本：{snapshot.ref.version_id}"
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures without claiming availability
            logger.warning("读取项目术语库状态失败 %s", self._identity, exc_info=True)
            status = f"项目术语库读取失败，请刷新重试或打开术语工作台检查。详情：{exc}"
            version = ""
        self._signals.finished.emit(self._generation, self._identity, status, version)


class ProjectTerminologyController(QObject):
    """Coalesce reads, reject stale context results and detach safely on close."""

    def __init__(self, parent, context, panel, *, can_open: bool, profile_controller=None) -> None:
        super().__init__(parent)
        self._context = context
        self._panel = panel
        self._can_open = can_open
        self._closed = False
        self._generation = 0
        self._reading = False
        self._signals = _ReadSignals()
        self._signals.finished.connect(self._ready, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)
        panel.refresh_button.clicked.connect(self.refresh)
        parent.installEventFilter(self)
        for name in ("project_changed", "variant_changed"):
            signal = getattr(context, name, None)
            if signal is not None:
                signal.connect(self.refresh)
        if profile_controller is not None:
            profile_controller.state_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args) -> None:
        if self._closed:
            return
        self._generation += 1
        identity = getattr(self._context, "active_version_identity", None)
        name = getattr(self._context, "project_name", None) or "当前工程"
        variant = getattr(self._context, "active_variant", None)
        self._panel.set_context(f"{name} · {variant}" if variant else name)
        self._panel.open_button.setEnabled(self._can_open and identity is not None)
        if identity is None:
            self._panel.render("未关联已保存的工程翻译版本；请先打开工程，再查看项目术语库。")
        elif getattr(self._context, "effective_terminology_factory", None) is None:
            self._panel.render("当前工程未接入项目术语服务；任务使用下方术语来源。")
        else:
            self._panel.render("正在读取当前项目术语库…")
        self._timer.start(0)

    def _start(self) -> None:
        if self._closed or self._reading:
            return
        identity = getattr(self._context, "active_version_identity", None)
        factory = getattr(self._context, "effective_terminology_factory", None)
        if identity is None or factory is None:
            return
        task = _ReadSummary(self._generation, identity, factory, self._signals)
        self._reading = True
        QThreadPool.globalInstance().start(task)

    @pyqtSlot(int, object, str, str)
    def _ready(self, generation, identity, status, version) -> None:
        self._reading = False
        if self._closed:
            return
        if generation != self._generation or identity != getattr(self._context, "active_version_identity", None):
            self.refresh()
            return
        self._panel.render(status, version)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if event.type() in (QEvent.Type.WindowActivate, QEvent.Type.Show):
            self.refresh()
        return super().eventFilter(watched, event)

    def close(self) -> None:
        self._closed = True
        self._timer.stop()


__all__ = ["ProjectTerminologyController"]
