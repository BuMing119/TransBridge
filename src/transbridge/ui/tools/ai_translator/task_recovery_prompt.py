"""Discover interrupted task manifests and offer explicit, nonblocking recovery."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from transbridge.ui.workers import ApiWorker

INTERRUPTED_STATES = frozenset({"preparing", "running", "applying", "pending_confirmation", "interrupted"})
RECOVERABLE_STATES = INTERRUPTED_STATES | {"partial", "failed"}


def matches_version(record, identity):
    return identity is not None and (str(record.get("project_id")), str(record.get("variant_id"))) == tuple(
        str(value) for value in identity
    )


def recovery_summary(record):
    total = sum(
        len(source.get("translate_keys", ())) + len(source.get("polish_keys", ())) for source in record["sources"]
    )
    completed = record.get("saved_count")
    progress = f"原任务范围 {total} 条"
    if completed is not None:
        progress += f" · 已保存校对候选 {completed} 条（继续时重新校验）"
    else:
        progress += " · 已保存进度将在继续时核验"
    return progress


def read_recovery_records(directory):
    from transbridge.application.translation.proofread_checkpoint import ProofreadCheckpoint
    from transbridge.application.translation.task_recovery import TaskRecoveryStore

    records = TaskRecoveryStore(directory / "ai-task-recovery").list_records()
    for record in records:
        if "checkpoint_settings" in record:
            record["saved_count"] = sum(
                ProofreadCheckpoint(
                    directory / "ai-proofread-resume",
                    project=record["project_id"],
                    variant=record["variant_id"],
                    source=source["key"],
                    settings=record["checkpoint_settings"],
                    task_id=record["task_id"],
                ).saved_count()
                for source in record["sources"]
            )
    return records


class TaskRecoveryPrompt(QDialog):
    deferred = pyqtSignal()

    def __init__(self, registry, record):
        super().__init__()
        self.setWindowTitle("发现未完成的校对任务")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        label = QLabel(
            f"时间：{record.get('created_at', '')[:19].replace('T', ' ')}\n"
            f"处理内容：{'、'.join(source['label'] for source in record['sources'])}\n"
            f"{recovery_summary(record)}\n\n"
            "继续任务会按原配置直接续跑，并重新核验已保存进度；重新开始会打开 AI 翻译配置界面，不复用原任务进度。"
        )
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons = QHBoxLayout()
        self.continue_button = QPushButton("继续任务")
        self.restart_button = QPushButton("重新开始")
        self.later_button = QPushButton("稍后处理")
        for button, restart in ((self.continue_button, False), (self.restart_button, True)):
            button.clicked.connect(lambda _checked=False, restart=restart: self._choose(registry, record, restart))
            buttons.addWidget(button)
        self.later_button.clicked.connect(self._defer)
        buttons.addWidget(self.later_button)
        layout.addLayout(buttons)

    def _defer(self):
        self.deferred.emit()
        self.reject()

    def _choose(self, registry, record, restart):
        if registry.request_recovery(record, restart=restart):
            self.accept()
        else:
            self.reject()


class TaskRecoveryDiscovery(QObject):
    def __init__(self, registry):
        super().__init__(registry)
        self.registry = registry
        self.worker = None
        self.dialogs = []
        self._scope = None
        self._seen = set()
        self._deferred = set()
        self._reoffer = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.discover)
        for name in ("project_changed", "variant_changed"):
            signal = getattr(registry.ctx, name, None)
            if signal is not None:
                signal.connect(self.schedule)
        self.schedule()

    def schedule(self, *_):
        if not self.registry.shutting_down:
            self.timer.start(0)

    def scope(self):
        directory = self.registry.directory_provider()
        identity = getattr(self.registry.ctx, "active_version_identity", None)
        return (Path(directory), tuple(identity)) if directory and identity else None

    def reoffer_deferred(self, on_clear):
        """Recheck postponed tasks before opening a new AI configuration window."""
        scope = self.scope()
        if self.registry.shutting_down or scope is None:
            return False
        if not any(saved_scope == scope for saved_scope, _ in self._deferred):
            return False
        for dialog in self.dialogs:
            if getattr(dialog, "recovery_scope", None) == scope and dialog.isVisible():
                dialog.raise_()
                dialog.activateWindow()
                return True
        self._reoffer = (scope, on_clear)
        self._scope = None
        self.schedule()
        return True

    def discover(self):
        if self.registry.shutting_down or self.worker is not None:
            return
        scope = self.scope()
        if scope == self._scope:
            return
        self._scope = scope
        reoffer, self._reoffer = self._reoffer, None
        for dialog in self.dialogs:
            dialog.close()
        if scope is None:
            return
        directory, identity = scope

        def read():
            return read_recovery_records(directory)

        def loaded(records):
            if self.registry.shutting_down or self.scope() != scope:
                return
            self.registry.recovery_error = ""
            shown = False
            eligible = set()
            for record in records:
                key = (directory, record["task_id"])
                deferred_key = (scope, record["task_id"])
                if (
                    self.registry.is_recovery_active(record["task_id"])
                    or not record.get("supported", True)
                    or record.get("state") not in INTERRUPTED_STATES
                    or not matches_version(record, identity)
                ):
                    continue
                eligible.add(deferred_key)
                if key in self._seen and not (reoffer and deferred_key in self._deferred):
                    continue
                self._seen.add(key)
                dialog = TaskRecoveryPrompt(self.registry, record)
                dialog.recovery_scope = scope
                dialog.deferred.connect(lambda key=deferred_key: self._deferred.add(key))
                dialog.accepted.connect(lambda key=deferred_key: self._deferred.discard(key))
                self.dialogs.append(dialog)
                dialog.show()
                shown = True
            self._deferred.difference_update({key for key in self._deferred if key[0] == scope and key not in eligible})
            if reoffer and reoffer[0] == scope and not shown:
                reoffer[1]()

        worker = ApiWorker(read, route_http_errors=False)
        self.worker = worker
        worker.result.connect(loaded)
        worker.error.connect(lambda error: self._failed(error) if self.scope() == scope else None)

        def finished():
            self.worker = None
            worker.deleteLater()
            if self.scope() != scope or self._reoffer is not None:
                self._scope = None
                self.schedule()

        worker.finished.connect(finished)
        worker.start()

    def _failed(self, error):
        self.registry.recovery_error = f"读取未完成任务失败：{error}"
        self.registry.changed.emit()
        if not self.registry.shutting_down:
            dialog = QMessageBox()
            dialog.setWindowTitle("任务恢复信息读取失败")
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText(self.registry.recovery_error)
            dialog.setInformativeText("可在 AI 任务记录中刷新重试。原任务和项目内容未被修改。")
            self.dialogs.append(dialog)
            dialog.show()

    def shutdown(self):
        self.timer.stop()
        self._reoffer = None
        for dialog in self.dialogs:
            dialog.close()
