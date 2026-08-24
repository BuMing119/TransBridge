"""AI 翻译进度窗口，支持暂停/继续/停止/后台运行。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ElidedLabel, reserve_text_width
from transbridge.ui.windowing import show_and_activate

from ._theme_support import AiThemeBinding, set_widget_brush

if TYPE_CHECKING:
    from transbridge.ui.context import AppContext
    from transbridge.ui.tools.ai_translator._translation_worker import _TranslationWorker
    from transbridge.ui.tools.ai_translator.task_adapter import LegacyAiTaskAdapter

from transbridge.ui.tools.ai_translator._translation_batch_widget import _BatchWidget


def _set_elided_text(label: ElidedLabel, text: str) -> None:
    label.set_full_text(text)
    label.setToolTip(text)
    label.setAccessibleDescription(text)


class _TranslationProgressWindow(QWidget):
    """翻译进行中的进度窗口，支持暂停/继续/后台/停止。"""

    translation_completed = pyqtSignal()

    def __init__(
        self,
        worker: _TranslationWorker,
        ctx: AppContext,
        parent=None,
        *,
        entry_activated=None,
        activity: LegacyAiTaskAdapter | None = None,
        theme_view: ThemeView | None = None,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self._worker = worker
        self._ctx = ctx
        self._entry_activated = entry_activated
        self._activity = activity
        self._theme_view = theme_view
        self._result_actions = None
        from .result_actions import AiResultNavigator

        self._result_navigator = AiResultNavigator()
        self.setWindowTitle("AI 自动翻译 — 进行中")
        self.resize(560, 600)
        self._background_mode = False
        self._collection_synced = False
        self._was_stopped = False
        self._close_after_stop = False
        self._log_viewer = None
        self._report_dialog = None
        self._batch_widgets: dict[int, _BatchWidget] = {}
        self._init_ui()
        self._theme_binding = AiThemeBinding(self, theme_view, self._apply_theme)
        self._connect_worker()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 进度区
        prog_box = QGroupBox("翻译进度")
        prog_layout = QVBoxLayout(prog_box)

        total_row = QHBoxLayout()
        total_lbl = QLabel("总进度:")
        total_lbl.setFixedWidth(52)
        self._total_progress_bar = QProgressBar()
        self._total_progress_bar.setRange(0, 100)
        self._total_progress_bar.setValue(0)
        self._total_progress_lbl = QLabel("0 / 0")
        self._total_progress_lbl.setFixedWidth(80)
        total_row.addWidget(total_lbl)
        total_row.addWidget(self._total_progress_bar)
        total_row.addWidget(self._total_progress_lbl)
        prog_layout.addLayout(total_row)

        self._progress_msg = ElidedLabel("准备中…")
        self._progress_msg.setAccessibleName("AI 翻译运行状态")
        _set_elided_text(self._progress_msg, "准备中…")
        prog_layout.addWidget(self._progress_msg)

        stats_row = QHBoxLayout()
        self._lbl_success = ElidedLabel("成功: 0")
        self._lbl_failed = ElidedLabel("失败: 0")
        self._lbl_terms = ElidedLabel("新增术语: 0")
        for lbl in (self._lbl_success, self._lbl_failed, self._lbl_terms):
            font = lbl.font()
            font.setBold(True)
            lbl.setFont(font)
            lbl.setAccessibleName(lbl.text().split(":", 1)[0])
            stats_row.addWidget(lbl, 1)
        prog_layout.addLayout(stats_row)

        layout.addWidget(prog_box)

        # 日志区
        log_box = QGroupBox("详细日志")
        log_layout = QVBoxLayout(log_box)

        # 轮次级日志（batch_idx=-1）
        self._round_log = QTextEdit()
        self._round_log.setReadOnly(True)
        self._round_log.setFont(QFont("Consolas", 9))
        self._round_log.setFixedHeight(70)
        log_layout.addWidget(self._round_log)

        # 批次子组件滚动区
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_content = QWidget()
        self._batches_layout = QVBoxLayout(self._scroll_content)
        self._batches_layout.setContentsMargins(0, 0, 0, 0)
        self._batches_layout.setSpacing(4)
        self._batches_layout.addStretch()
        self._scroll.setWidget(self._scroll_content)
        self._scroll.setMinimumHeight(220)
        log_layout.addWidget(self._scroll)

        layout.addWidget(log_box)

        # 按钮行
        btn_row = QHBoxLayout()
        self._pause_btn = QPushButton("⏸ 暂停")
        reserve_text_width(self._pause_btn, ("⏸ 暂停", "▶ 继续"))
        self._pause_btn.clicked.connect(self._on_pause_resume)
        self._stop_btn = QPushButton("⏹ 停止")
        self._stop_btn.clicked.connect(self._on_stop)
        self._llm_log_btn = QPushButton("📄 LLM 日志")
        self._llm_log_btn.clicked.connect(self._on_open_log_viewer)
        btn_row.addWidget(self._pause_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._llm_log_btn)
        layout.addLayout(btn_row)

    def _connect_worker(self):
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(self._on_log)
        self._worker.result.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_finished)

    # ── 槽 ────────────────────────────────────────────────────────────────────

    def _on_progress(self, current: int, total: int, message: str, success: int, failed: int, new_terms: int):
        if self._activity is not None:
            self._activity.progress(current, total, message)
        if total > 0:
            self._total_progress_bar.setMaximum(total)
            self._total_progress_bar.setValue(current)
            self._total_progress_lbl.setText(f"{current} / {total}")
        _set_elided_text(self._progress_msg, message)
        _set_elided_text(self._lbl_success, f"成功: {success}")
        _set_elided_text(self._lbl_failed, f"失败: {failed}")
        _set_elided_text(self._lbl_terms, f"新增术语: {new_terms}")
        if not message.endswith("]"):
            sb = self._round_log.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            self._round_log.append(f"▶ {message}")
            if at_bottom:
                sb.setValue(sb.maximum())

    def _on_log(self, batch_idx: int, line: str):
        if batch_idx < 0:
            sb = self._round_log.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            self._round_log.append(line)
            if at_bottom:
                sb.setValue(sb.maximum())
            return

        # 获取或创建批次组件
        if batch_idx not in self._batch_widgets:
            w = _BatchWidget(batch_idx)
            self._batch_widgets[batch_idx] = w
            # 插入在末尾 stretch 之前
            count = self._batches_layout.count()
            self._batches_layout.insertWidget(count - 1, w)

        widget = self._batch_widgets[batch_idx]
        sb = self._scroll.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        widget.append_line(line)

        # 仅在用户已在底部时才跟随滚动（等 layout 更新后再读 maximum）
        if at_bottom:
            QTimer.singleShot(
                0, lambda: self._scroll.verticalScrollBar().setValue(self._scroll.verticalScrollBar().maximum())
            )

    def _on_open_log_viewer(self):
        """打开/激活 LLM 流式日志查看窗口。"""
        from transbridge.ui.tools.ai_translator._llm_log_viewer import _LLMLogViewer

        path = self._worker.stream_log_dir
        if not path:
            return
        if self._log_viewer is None or not self._log_viewer.isVisible():
            self._log_viewer = _LLMLogViewer(path, parent=None)
        show_and_activate(self._log_viewer)

    def _on_result(self, result):
        if self._activity is not None:
            self._was_stopped |= str(self._activity.activity.state) == "cancelling"
            self._activity.finish(cancelled=self._was_stopped)
        self._total_progress_bar.setMaximum(100)
        self._total_progress_bar.setValue(100)
        self._total_progress_lbl.setText("完成")

        # 折叠所有未完成的批次组件
        for w in self._batch_widgets.values():
            w.force_collapse()

        # 构建后处理摘要（如果有）
        pp_summary = ""
        if hasattr(result, "post_process_result") and result.post_process_result:
            pp = result.post_process_result
            error_count = sum(1 for i in pp.issues if i.severity == "error")
            warning_count = sum(1 for i in pp.issues if i.severity == "warning")
            pp_summary = f"\n\n质量检查：{pp.total_checked} 条"
            if error_count > 0 or warning_count > 0:
                pp_summary += f"（{error_count} 错误，{warning_count} 警告）"
            if pp.needs_review:
                pp_summary += f"\n需审核：{len(pp.needs_review)} 条"

        if self._was_stopped:
            _set_elided_text(self._progress_msg, "已停止")
            _set_elided_text(self._lbl_success, f"成功: {result.success_count}")
            _set_elided_text(self._lbl_failed, f"失败: {result.failed_count}")
            _set_elided_text(self._lbl_terms, f"新增术语: {result.new_dynamic_terms}")
            self._round_log.append(
                f"\n⏹ 已停止 — 成功 {result.success_count} 条，"
                f"失败 {result.failed_count} 条，新增术语 {result.new_dynamic_terms} 个"
            )
        else:
            _set_elided_text(self._progress_msg, "翻译完成")
            _set_elided_text(self._lbl_success, f"成功: {result.success_count}")
            _set_elided_text(self._lbl_failed, f"失败: {result.failed_count}")
            _set_elided_text(self._lbl_terms, f"新增术语: {result.new_dynamic_terms}")
            self._round_log.append(
                f"\n✅ 完成 — 成功 {result.success_count} 条，"
                f"失败 {result.failed_count} 条，新增术语 {result.new_dynamic_terms} 个"
            )
            if pp_summary:
                self._round_log.append(pp_summary.strip())

        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._collection_synced = True
        if self._log_viewer is not None:
            self._log_viewer.stop_auto_refresh()
        self._ctx.collection_changed.emit(self._ctx.collection)
        self.translation_completed.emit()

        # ── 生成报告 ──
        esp_stem = self._get_esp_stem()
        report_path = None
        try:
            from transbridge.ai_translator.post_processor.report_generator import ReportGenerator

            gen = ReportGenerator(esp_stem)
            report_path = gen.generate_translate_report(
                result,
                refine_results=getattr(result, "refine_results", None),
                polish_results=getattr(result, "polish_results", None),
                decisions=getattr(result, "decisions", None),
            )
            result.report_path = report_path
        except Exception:
            pass  # 报告生成失败不阻塞流程
        if self._activity is not None:
            from .result_actions import result_action_state

            spec = self._activity.activity.spec
            artifact = self._result_navigator.register_report(spec, report_path)
            self._result_actions = result_action_state(spec, result=result, report=artifact)

        # ── 弹出报告对话框（替代 QMessageBox）──
        if not self._background_mode and not self._was_stopped:
            self._show_report_dialog(result, report_path)
        elif self._was_stopped and not self._background_mode:
            # 停止时仍显示报告（基于已完成条目）
            self._show_report_dialog(result, report_path)

    def _on_error(self, err: str):
        if self._activity is not None:
            self._activity.fail(err)
        for w in self._batch_widgets.values():
            w.force_collapse()
        _set_elided_text(self._progress_msg, f"错误: {err}")
        self._round_log.append(f"\n❌ 全局错误: {err}")
        self._round_log.verticalScrollBar().setValue(self._round_log.verticalScrollBar().maximum())
        self._collection_synced = True
        self._ctx.collection_changed.emit(self._ctx.collection)
        QMessageBox.critical(self, "翻译错误", err)

    def _on_worker_finished(self):
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        if not self._collection_synced:
            self._collection_synced = True
            if self._log_viewer is not None:
                self._log_viewer.stop_auto_refresh()
            self._ctx.collection_changed.emit(self._ctx.collection)

    def _on_pause_resume(self):
        if self._worker.is_paused:
            self._worker.resume()
            getattr(self._activity, "resume", lambda: None)()
            self._pause_btn.setText("⏸ 暂停")
            _set_elided_text(self._progress_msg, "已继续 - 等待下一批")
            self._round_log.append("▶ 已继续")
        else:
            self._worker.pause()
            getattr(self._activity, "pause", lambda: None)()
            self._pause_btn.setText("▶ 继续")
            _set_elided_text(self._progress_msg, "⏸ 已暂停（当前 API 调用将立即中断）")
            self._round_log.append("⏸ 已暂停")
            self._ctx.collection_changed.emit(self._ctx.collection)

    def _on_stop(self):
        reply = QMessageBox.question(
            self,
            "停止翻译",
            "确定要停止翻译吗？\n已翻译的内容不会丢失，可通过断点续传继续。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._was_stopped = True
            if self._activity is not None:
                self._activity.request_cancel()
            _set_elided_text(self._progress_msg, "⏹ 正在停止（当前 API 调用将立即中断）")
            self._round_log.append("⏹ 已请求停止")
            self._worker.stop()
            self._stop_btn.setEnabled(False)
            self._pause_btn.setEnabled(False)

    # ── 关闭事件 ──────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._worker.isRunning()

    def closeEvent(self, event):
        if self._close_after_stop and self._worker.isRunning():
            event.ignore()
            return
        if not self._worker.isRunning():
            self._theme_binding.close()
            event.accept()
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("翻译仍在进行中")
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.addWidget(QLabel("翻译仍在进行中，请选择操作："))

        bg = QButtonGroup(dlg)
        rb_stop = QRadioButton("停止翻译并关闭")
        rb_bg = QRadioButton("后台继续，关闭窗口")
        rb_stop.setChecked(True)
        bg.addButton(rb_stop)
        bg.addButton(rb_bg)
        dlg_layout.addWidget(rb_stop)
        dlg_layout.addWidget(rb_bg)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            event.ignore()
            return

        if rb_stop.isChecked():
            self._was_stopped = True
            if self._activity is not None:
                self._activity.request_cancel()
            self._worker.stop()
            self._close_after_stop = True
            self.setEnabled(False)
            self._total_progress_bar.setRange(0, 0)
            self.setWindowTitle("AI 自动翻译 — 正在停止…")
            self._worker.finished.connect(self._close_after_worker_stopped)
            event.ignore()
        else:
            self._background_mode = True
            event.accept()

    def _close_after_worker_stopped(self) -> None:
        if self._close_after_stop:
            self.close()

    def _get_esp_stem(self) -> str:
        """获取当前翻译的 ESP stem（用于报告目录）。"""
        return self._worker.esp_stem

    @property
    def activity(self):
        return None if self._activity is None else self._activity.activity

    @property
    def task_activity(self):
        return None if self._activity is None else self._activity.task_activity

    @property
    def result_actions(self):
        return self._result_actions

    def _show_report_dialog(self, result, report_path: str | None):
        """弹出翻译报告对话框。"""
        from ._translation_report_dialog import _TranslationReportDialog

        dialog = _TranslationReportDialog(
            translate_result=result,
            refine_results=getattr(result, "refine_results", None),
            polish_results=getattr(result, "polish_results", None),
            decisions=getattr(result, "decisions", None),
            report_path=report_path,
            parent=self,
            theme_view=getattr(self, "_theme_view", None),
        )
        if self._entry_activated is not None:
            dialog.entry_activated.connect(self._entry_activated)
        self._report_dialog = dialog
        show_and_activate(dialog)

    def _apply_theme(self, binding: AiThemeBinding) -> None:
        message = self._progress_msg.full_text
        if message.startswith(("错误", "失败")):
            task_key = "failed"
        elif "完成" in message:
            task_key = "completed"
        elif "暂停" in message:
            task_key = "paused"
        else:
            task_key = "running"
        set_widget_brush(self._progress_msg, binding.task(task_key))
        set_widget_brush(self._lbl_success, binding.report("success"))
        set_widget_brush(self._lbl_failed, binding.report("error"))
        set_widget_brush(self._lbl_terms, binding.report("info"))

    @property
    def theme_revision(self) -> int:
        return self._theme_binding.revision
