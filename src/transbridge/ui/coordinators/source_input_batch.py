"""Keep one operation's missing-input list open while explicit files are supplied."""

from collections import Counter, deque

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog

from transbridge.application.projects.source_input_candidates import (
    InputNeed,
    collect_input_files,
    match_input_candidates,
)
from transbridge.ui.source_input_batch_dialog import SourceInputBatchDialog
from transbridge.ui.source_input_state import missing_source_keys


class SourceInputBatchCoordinator:
    def __init__(self, host, inputs):
        self.host, self.inputs = host, inputs
        self.dialog = None
        self._queue = deque()
        self._working = False

    def ensure(
        self,
        source_keys,
        *,
        resume,
        parent=None,
        on_finished=None,
        is_current=None,
        on_scope_ready=None,
        allow_partial=False,
        operation_label="本次写回",
    ):
        keys = tuple(self.host.context.slots) if source_keys is None else self.inputs.resolved_keys(source_keys)
        if not missing_source_keys(self.host.context, "template", keys):
            return True
        if self.dialog is not None or self.inputs._busy or self._working:
            if self.dialog is not None:
                self.dialog.raise_()
                self.dialog.activateWindow()
            if on_finished is not None:
                on_finished(False)
            return False
        self._keys = keys
        self._identity = self.host.context.active_version_identity
        self._owner_current = is_current
        self._resume, self._finished, self._scope_ready = resume, on_finished, on_scope_ready
        self._allow_partial = allow_partial and on_scope_ready is not None
        self._candidates = {}
        self._discovery_errors = ()
        self._stop_requested = False
        self._queue.clear()
        self.dialog = SourceInputBatchDialog(
            self._needs(),
            parent or self.host,
            requirement="template",
            allow_partial=self._allow_partial,
            operation_label=operation_label,
        )
        self.dialog.files_requested.connect(self._choose_files)
        self.dialog.directory_requested.connect(self._choose_directory)
        self.dialog.source_requested.connect(self._choose_source)
        self.dialog.candidate_requested.connect(self._supply)
        self.dialog.continued.connect(self._continue)
        self.dialog.rejected.connect(self._cancel)
        self.dialog.open()
        return False

    def _current(self):
        return (
            self.dialog is not None
            and not sip.isdeleted(self.dialog)
            and not getattr(self.host, "close_pending", False)
            and self.host.context.active_version_identity == self._identity
            and (self._owner_current is None or self._owner_current())
        )

    def _needs(self):
        slots = self.host.context.slots
        result = []
        for old in self._keys:
            key = self.inputs.resolved_keys((old,))[0]
            slot = slots.get(key)
            if slot is None:
                continue
            # UI identity stays stable across the project reopen after supplementation.
            result.append(
                InputNeed(
                    old,
                    slot.label,
                    len(slot.collection),
                    getattr(slot, "source_snapshot", None) is not None,
                    next(
                        (
                            str(getattr(slot, field))
                            for field in ("esp_path", "eet_path", "xt_path")
                            if getattr(slot, field, None)
                        ),
                        "",
                    ),
                )
            )
        return tuple(result)

    def _choose_files(self):
        if not self._current() or self._working:
            return
        paths, _ = QFileDialog.getOpenFileNames(self.dialog, "选择本次写回需要的源文件或模板", "", "来源文件 (*)")
        if paths:
            self._discover(paths=tuple(paths))

    def _choose_directory(self):
        if not self._current() or self._working:
            return
        directory = QFileDialog.getExistingDirectory(self.dialog, "查找本次缺少的来源文件（包含子文件夹）")
        if directory:
            self._discover(directory=directory)

    def _choose_source(self, key):
        if not self._current() or self._working:
            return
        need = next((item for item in self._needs() if item.key == key), None)
        if need is None:
            return
        path, _ = QFileDialog.getOpenFileName(self.dialog, f"补充 {need.label}", "", "来源文件 (*)")
        if path:
            self._supply(key, path)

    def _discover(self, *, paths=(), directory=None):
        self._set_busy(True)
        started = self.host.start_foreground_task(
            lambda: collect_input_files(paths, directory=directory),
            message="正在查找所选位置中的来源文件…",
            on_result=self._discovered,
            on_error=self._error,
            disable_workbench=False,
        )
        if not started:
            self._error("另一项操作仍在进行，请稍后重新选择输入。")

    def _discovered(self, result):
        if self._stop_requested:
            self._set_busy(False)
            self._cancel()
            return
        if not self._current():
            self._set_busy(False)
            self._cancel()
            return
        matched = match_input_candidates(self._needs(), result.paths)
        for key, paths in matched.items():
            if paths:
                self._candidates[key] = tuple(dict.fromkeys((*self._candidates.get(key, ()), *paths)))
        self.dialog.set_candidates(self._candidates)
        self._discovery_errors = result.errors
        counts = Counter(path for paths in self._candidates.values() for path in paths)
        for need in self._needs():
            candidates = self._candidates.get(need.key, ())
            if not need.ready and len(candidates) == 1 and counts[candidates[0]] == 1:
                self._queue.append((need.key, candidates[0]))
        self._set_busy(False)
        if result.errors:
            self._feedback("部分位置无法读取，已保留其他候选。")
        elif not any(matched.values()):
            self._feedback("没有找到同名候选。可以为清单中的项目单独选择文件；已有补充保持不变。")
        else:
            self._feedback("同名且唯一的输入将逐项补充；多个候选请选定版本后补充。")
        self._next()

    def _next(self):
        if not self._current():
            self._cancel()
            return
        if self._queue:
            key, path = self._queue.popleft()
            self._supply(key, path)

    def _supply(self, key, path):
        if not self._current() or self._working:
            return
        actual = self.inputs.resolved_keys((key,))[0]
        if key not in self._keys or actual not in self.host.context.slots:
            self._error("原操作的来源已变化，请重新发起写回。")
            return
        if not missing_source_keys(self.host.context, "template", (actual,)):
            QTimer.singleShot(0, self._next)
            return
        self._set_busy(True)
        self._feedback(f"正在补充 {self.host.context.slots[actual].label}…")

        def finished(success):
            self._set_busy(False)
            if self._stop_requested or not self._current():
                self._cancel()
                return
            self.dialog.set_needs(self._needs())
            if success:
                self._candidates.pop(key, None)
                self.dialog.set_candidates(self._candidates)
                self._feedback("输入已保存。可以继续补充，或确认本次写回范围。")
            else:
                self._queue.clear()
                self._feedback("本项尚未补充，已完成的输入仍保留。可重新选择文件或稍后处理。")
            QTimer.singleShot(0, self._next)

        self.inputs._ensure_one(
            "template",
            (actual,),
            resume=lambda: None,
            parent=self.dialog,
            on_finished=finished,
            is_current=self._current,
            selected_key=actual,
            selected_path=path,
            auto_match=True,
            report_error=self._error,
        )

    def _set_busy(self, busy):
        self._working = busy
        if self.dialog is not None and not sip.isdeleted(self.dialog):
            self.dialog.set_busy(busy)

    def _error(self, message):
        self._queue.clear()
        self._set_busy(False)
        if self._stop_requested:
            self._cancel()
        elif self._current():
            self._feedback(str(message), error=True)
        else:
            self._cancel()

    def _feedback(self, message, *, error=False):
        issues = "\n".join(self._discovery_errors)
        self.dialog.set_feedback(f"{message}\n{issues}" if issues else message, error=error or bool(issues))

    def _continue(self, selected):
        if not self._current() or self._working:
            self._cancel()
            return
        needs = self._needs()
        ready = {need.key for need in needs if need.ready}
        selected = tuple(selected)
        if (
            {need.key for need in needs} != set(self._keys)
            or not selected
            or not set(selected) <= ready
            or not set(selected) <= set(self._keys)
            or (not self._allow_partial and set(selected) != set(self._keys))
        ):
            self.dialog.set_needs(self._needs())
            self.dialog.set_feedback("来源准备状态已变化，请重新确认范围。", error=True)
            return
        keys = self.inputs.resolved_keys(selected)
        resume, scoped = self._resume, self._scope_ready
        identity, owner_current = self._identity, self._owner_current
        self._close(True)

        def resume_confirmed():
            if (
                getattr(self.host, "close_pending", False)
                or self.host.context.active_version_identity != identity
                or (owner_current is not None and not owner_current())
                or any(key not in self.host.context.slots for key in keys)
                or missing_source_keys(self.host.context, "template", keys)
            ):
                return
            scoped(keys) if scoped is not None else resume()

        QTimer.singleShot(0, resume_confirmed)

    def _cancel(self):
        self._queue.clear()
        if self._working:
            # Finish publishing/reopening the current item before releasing this
            # session. A user cancellation never launches the remaining queue.
            self._stop_requested = True
            return
        self._close(False)

    def _close(self, completed):
        dialog, self.dialog = self.dialog, None
        if dialog is None:
            return
        if not sip.isdeleted(dialog):
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()
        finished, self._finished = self._finished, None
        if finished is not None:
            finished(completed)
