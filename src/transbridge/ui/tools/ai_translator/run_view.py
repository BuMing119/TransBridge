"""Detailed progress surfaces for proofreading, mixed, and custom AI workflows."""

from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ElidedLabel, reserve_text_width
from transbridge.ui.windowing import show_and_activate

from ._llm_log_viewer import _LLMLogViewer
from ._theme_support import AiThemeBinding, set_widget_brush
from .workflow_progress import WorkflowProgress, stages_for_profile


class _StageProgressRow(QWidget):
    def __init__(self, label: str, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(f"{label}：")
        self.label.setFixedWidth(52)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.count = QLabel("0 / 0")
        self.count.setFixedWidth(80)
        layout.addWidget(self.label)
        layout.addWidget(self.progress, 1)
        layout.addWidget(self.count)

    def update_progress(self, current: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(current)
            self.count.setText(f"{current} / {total}")
        else:
            self.progress.setRange(0, 0)
            self.count.setText("准备中")

    def complete(self) -> None:
        if self.progress.maximum() <= 0:
            self.progress.setRange(0, 1)
        self.progress.setValue(self.progress.maximum())
        if self.count.text() in {"准备中", "0 / 0"}:
            self.count.setText("已跳过")


class AiWorkflowProgressWindow(QWidget):
    """One information hierarchy for proofreading and mixed workflow runs."""

    def __init__(
        self,
        worker: object,
        activity: object,
        *,
        title: str,
        workflow_summary: str,
        stages: tuple[tuple[str, str], ...],
        auxiliary_stat: str = "新增术语",
        sequential: bool = True,
        parent=None,
        theme_view: ThemeView | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._worker = worker
        self._activity = activity
        self._result_actions = None
        self._report_diagnostics: tuple[str, ...] = ()
        self._log_viewer: _LLMLogViewer | None = None
        self._terminal = False
        self._stage_rows: dict[str, _StageProgressRow] = {}
        self._stage_order = tuple(key for key, _label in stages)
        self._sequential = sequential
        self._auxiliary_stat = auxiliary_stat
        self.setWindowTitle(f"{title} — 进行中")
        self.resize(620, 540)

        layout = QVBoxLayout(self)
        summary = ElidedLabel(f"执行流程：{workflow_summary}")
        summary.setAccessibleName("AI 实际执行流程")
        summary.set_full_text(summary.text())
        summary.setToolTip(summary.full_text)
        summary.setAccessibleDescription(summary.full_text)
        layout.addWidget(summary)

        progress_box = QGroupBox("运行进度")
        progress_layout = QVBoxLayout(progress_box)
        total_row = QHBoxLayout()
        total_row.addWidget(QLabel("总进度："))
        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        self._progress.setFormat("%p%")
        self._total_progress_lbl = QLabel("0%")
        self._total_progress_lbl.setFixedWidth(52)
        total_row.addWidget(self._progress, 1)
        total_row.addWidget(self._total_progress_lbl)
        progress_layout.addLayout(total_row)

        self._status = ElidedLabel("准备中…")
        self._status.setAccessibleName("AI 工作流运行状态")
        self._set_status("准备中…")
        progress_layout.addWidget(self._status)

        stats_row = QHBoxLayout()
        self._lbl_success = ElidedLabel("成功: 0")
        self._lbl_failed = ElidedLabel("失败: 0")
        self._lbl_pending = ElidedLabel("待审: 0")
        self._lbl_terms = ElidedLabel(f"{auxiliary_stat}: 0")
        for label in (self._lbl_success, self._lbl_failed, self._lbl_pending, self._lbl_terms):
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            label.setAccessibleName(label.text().split(":", 1)[0])
            stats_row.addWidget(label, 1)
        progress_layout.addLayout(stats_row)
        layout.addWidget(progress_box)

        stages_box = QGroupBox("阶段进度")
        stages_layout = QVBoxLayout(stages_box)
        for key, label in stages:
            row = _StageProgressRow(label)
            self._stage_rows[key] = row
            stages_layout.addWidget(row)
        layout.addWidget(stages_box)

        log_box = QGroupBox("详细日志")
        log_layout = QVBoxLayout(log_box)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        self._log.setMinimumHeight(170)
        log_layout.addWidget(self._log)
        layout.addWidget(log_box, 1)

        button_row = QHBoxLayout()
        self._pause = QPushButton("⏸ 暂停")
        reserve_text_width(self._pause, ("⏸ 暂停", "▶ 继续"))
        self._pause.setEnabled(callable(getattr(worker, "pause", None)))
        self._pause.clicked.connect(self._on_pause_resume)
        self._stop = QPushButton("⏹ 停止")
        self._stop.clicked.connect(self._request_stop)
        self._llm_log_btn = QPushButton("📄 LLM 日志")
        self._llm_log_btn.setToolTip("查看本次运行的 LLM 原始输出与各处理阶段诊断")
        self._llm_log_btn.setEnabled(bool(getattr(worker, "stream_log_dir", "")))
        self._llm_log_btn.clicked.connect(self._on_open_log_viewer)
        button_row.addWidget(self._pause)
        button_row.addWidget(self._stop)
        button_row.addStretch()
        button_row.addWidget(self._llm_log_btn)
        layout.addLayout(button_row)
        if log_error := str(getattr(worker, "stream_log_error", "") or ""):
            self._show_log_error(log_error)
        self._theme_binding = AiThemeBinding(self, theme_view, self._apply_theme)

    def is_running(self) -> bool:
        if self._terminal or _qt_object_deleted(self._worker):
            return False
        try:
            return bool(self._worker.isRunning())
        except RuntimeError:
            if _qt_object_deleted(self._worker):
                return False
            raise

    @property
    def result_actions(self):
        return self._result_actions

    @property
    def task_activity(self):
        return self._activity.task_activity

    def set_result_actions(self, state: object) -> None:
        self._result_actions = state

    def set_report_diagnostics(self, diagnostics: tuple[str, ...]) -> None:
        self._report_diagnostics = diagnostics
        if not diagnostics:
            return
        self._set_status("已完成（报表生成有警告）")
        details = "\n".join(diagnostics)
        self._status.setToolTip(details)
        self._status.setAccessibleDescription(details)
        for diagnostic in diagnostics:
            self.append_log(f"⚠ {diagnostic}")

    def apply_progress(self, value: WorkflowProgress) -> None:
        if value.stage == "done":
            for row in self._stage_rows.values():
                row.complete()
        elif row := self._stage_rows.get(value.stage):
            if self._sequential:
                index = self._stage_order.index(value.stage)
                for previous in self._stage_order[:index]:
                    self._stage_rows[previous].complete()
            row.update_progress(value.current, value.total)
        self._progress.setValue(max(self._progress.value(), value.overall_current))
        self._total_progress_lbl.setText(f"{self._progress.value() // 10}%")
        self._set_status(f"{value.stage_label}：{value.message}" if value.stage != "done" else value.message)
        if value.stage == "terms":
            self._set_stat(self._lbl_success, "完成批次", value.success)
            self._set_stat(self._lbl_failed, "失败批次", value.failed)
            self._set_stat(self._lbl_pending, "剩余批次", value.pending)
            self._set_stat(self._lbl_terms, "候选术语", value.new_terms)
        else:
            self._set_stat(self._lbl_success, "成功", value.success)
            self._set_stat(self._lbl_failed, "失败", value.failed)
            self._set_stat(self._lbl_pending, "待审", value.pending)
            auxiliary_value = value.issues if self._auxiliary_stat == "问题" else value.new_terms
            self._set_stat(self._lbl_terms, self._auxiliary_stat, auxiliary_value)
        self.append_log(f"[{value.stage_label}] {value.message}")

    def append_log(self, line: str) -> None:
        if not line:
            return
        scrollbar = self._log.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self._log.append(line)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def mark_finished(self) -> None:
        self._terminal = True
        self._stop_log_refresh()
        self._pause.setEnabled(False)
        self._stop.setEnabled(False)
        self._progress.setValue(self._progress.maximum())
        self._total_progress_lbl.setText("100%")
        for row in self._stage_rows.values():
            row.complete()
        self._set_status("已完成")

    def mark_error(self, message: str) -> None:
        self._terminal = True
        self._stop_log_refresh()
        self._pause.setEnabled(False)
        self._stop.setEnabled(False)
        self._set_status(f"失败：{message}")
        self.append_log(f"❌ {message}")

    def mark_cancelled(self) -> None:
        self._terminal = True
        self._stop_log_refresh()
        self._pause.setEnabled(False)
        self._stop.setEnabled(False)
        self._set_status("已停止")
        self.append_log("⏹ 已停止")

    def _request_stop(self) -> None:
        self._activity.request_cancel()
        cancel = getattr(self._worker, "cancel", None)
        stop = getattr(self._worker, "stop", None)
        if callable(cancel):
            cancel()
        elif callable(stop):
            stop()
        self._pause.setEnabled(False)
        self._stop.setEnabled(False)
        self._set_status("正在等待安全停止点")
        self.append_log("⏹ 已请求停止，正在等待当前调用结束")

    def _on_pause_resume(self) -> None:
        if bool(getattr(self._worker, "is_paused", False)):
            self._worker.resume()
            getattr(self._activity, "resume", lambda: None)()
            self._pause.setText("⏸ 暂停")
            self._set_status("已继续，等待下一安全进度点")
            self.append_log("▶ 已继续")
        else:
            self._worker.pause()
            getattr(self._activity, "pause", lambda: None)()
            self._pause.setText("▶ 继续")
            self._set_status("已暂停（当前 API 调用将在安全点中断）")
            self.append_log("⏸ 已暂停")

    def _on_open_log_viewer(self) -> None:
        log_dir = str(getattr(self._worker, "stream_log_dir", "") or "")
        if not log_dir:
            self._show_log_error(str(getattr(self._worker, "stream_log_error", "") or "未知文件错误"))
            return
        if self._log_viewer is None:
            self._log_viewer = _LLMLogViewer(log_dir, parent=None)
        if self._terminal:
            self._log_viewer.stop_auto_refresh()
        show_and_activate(self._log_viewer)

    def _stop_log_refresh(self) -> None:
        if self._log_viewer is not None:
            self._log_viewer.stop_auto_refresh()

    def _show_log_error(self, error: str) -> None:
        message = f"LLM 日志不可用：{error}"
        self._llm_log_btn.setEnabled(False)
        self._llm_log_btn.setToolTip(message)
        self.append_log(f"⚠ {message}")

    def _set_status(self, text: str) -> None:
        self._status.set_full_text(text)
        self._status.setToolTip(text)
        self._status.setAccessibleDescription(text)

    @staticmethod
    def _set_stat(label: ElidedLabel, name: str, value: int | None) -> None:
        if value is None:
            return
        text = f"{name}: {value}"
        label.set_full_text(text)
        label.setAccessibleName(name)
        label.setToolTip(text)
        label.setAccessibleDescription(text)

    def closeEvent(self, event) -> None:
        if self.is_running():
            self.hide()
            event.ignore()
            return
        self._theme_binding.close()
        event.accept()

    def _apply_theme(self, binding: AiThemeBinding) -> None:
        text = self._status.full_text
        if text.startswith("失败"):
            key = "failed"
        elif text.startswith("已完成"):
            key = "completed"
        elif text.startswith("已停止"):
            key = "cancelled"
        elif "暂停" in text:
            key = "paused"
        else:
            key = "running"
        set_widget_brush(self._status, binding.task(key))
        set_widget_brush(self._lbl_success, binding.report("success"))
        set_widget_brush(self._lbl_failed, binding.report("error"))
        set_widget_brush(self._lbl_pending, binding.report("warning"))
        set_widget_brush(self._lbl_terms, binding.report("info"))

    @property
    def theme_revision(self) -> int:
        return self._theme_binding.revision


class AiMixedProgressWindow(AiWorkflowProgressWindow):
    """Compatibility entry point backed by the unified detailed workflow view."""

    def __init__(
        self,
        worker: object,
        activity: object,
        parent=None,
        *,
        profile: object | None = None,
        theme_view: ThemeView | None = None,
    ) -> None:
        if profile is None:
            stages = (("translate", "翻译"),) + stages_for_profile(None, include_translation=False)
            summary = "翻译 → 检测 → 修复 → 润色 → 裁决"
        else:
            stages = tuple(getattr(worker, "progress_stages", stages_for_profile(profile, include_translation=True)))
            summary = " → ".join(label for _key, label in stages) or "未启用处理阶段"
        super().__init__(
            worker,
            activity,
            title="AI 混合运行",
            workflow_summary=summary,
            stages=stages,
            sequential=str(getattr(worker, "execution_order", "serial")) != "parallel",
            parent=parent,
            theme_view=theme_view,
        )
        worker.progress.connect(self._on_progress)
        log_signal = getattr(worker, "log", None)
        if log_signal is not None:
            log_signal.connect(self.append_log)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        worker.cancelled.connect(self._on_cancelled)

    def _on_progress(self, value: object) -> None:
        if isinstance(value, WorkflowProgress):
            self.apply_progress(value)
            return
        current = int(getattr(value, "translate_done", 0)) + int(getattr(value, "polish_done", 0))
        total = int(getattr(value, "translate_total", 0)) + int(getattr(value, "polish_total", 0))
        stage = str(getattr(value, "stage", "translate"))
        overall = round(current / total * 1000) if total else 0
        self.apply_progress(
            WorkflowProgress(
                stage=stage,
                stage_label={"translate": "翻译", "polish": "润色", "done": "完成"}.get(stage, stage),
                current=current,
                total=total,
                message=stage,
                overall_current=overall,
                success=int(getattr(value, "translate_success", 0)) + int(getattr(value, "polish_success", 0)),
                failed=int(getattr(value, "translate_failed", 0)) + int(getattr(value, "polish_failed", 0)),
            )
        )

    def _on_finished(self, _result: object) -> None:
        self.mark_finished()

    def _on_error(self, message: str) -> None:
        self.mark_error(message)

    def _on_cancelled(self) -> None:
        self.mark_cancelled()


__all__ = ["AiMixedProgressWindow", "AiWorkflowProgressWindow"]


def _qt_object_deleted(value: object) -> bool:
    try:
        return sip.isdeleted(value)
    except TypeError:
        return False
