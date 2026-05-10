"""
批量翻译进度窗口。

支持：
- 显示总体进度（插件 X/Y）
- 显示当前插件的详细进度
- 暂停/继续/停止
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QProgressBar, QPushButton, QMessageBox, QDialog,
    QDialogButtonBox, QTextEdit, QScrollArea, QRadioButton, QButtonGroup,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont

from ._translation_progress_window import _BatchWidget

if TYPE_CHECKING:
    from src.transbridge.ui.context import AppContext
    from ._batch_translation_worker import _BatchTranslationWorker, BatchTranslationSummary


class _BatchTranslationProgressWindow(QWidget):
    """批量翻译进度窗口。"""

    translation_completed = pyqtSignal()

    def __init__(
        self,
        worker: "_BatchTranslationWorker",
        ctx: "AppContext",
        parent=None,
    ):
        super().__init__(parent, Qt.WindowType.Window)
        self._worker = worker
        self._ctx = ctx
        self.setWindowTitle("AI 批量翻译 — 进行中")
        self.resize(560, 650)
        self._background_mode = False
        self._current_plugin_idx = 0
        self._total_plugins = 0
        self._was_stopped = False
        self._log_viewer = None
        self._batch_widgets: dict[int, _BatchWidget] = {}
        self._init_ui()
        self._connect_worker()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 总体进度区
        overall_box = QGroupBox("批量翻译进度")
        overall_layout = QVBoxLayout(overall_box)

        overall_row = QHBoxLayout()
        overall_lbl = QLabel("总体进度:")
        overall_lbl.setFixedWidth(52)
        self._overall_progress_bar = QProgressBar()
        self._overall_progress_bar.setRange(0, 100)
        self._overall_progress_bar.setValue(0)
        self._overall_progress_lbl = QLabel("0 / 0 插件")
        self._overall_progress_lbl.setFixedWidth(80)
        overall_row.addWidget(overall_lbl)
        overall_row.addWidget(self._overall_progress_bar)
        overall_row.addWidget(self._overall_progress_lbl)
        overall_layout.addLayout(overall_row)

        self._overall_msg = QLabel("准备中…")
        self._overall_msg.setStyleSheet("font-size: 11px; color: #555;")
        overall_layout.addWidget(self._overall_msg)

        layout.addWidget(overall_box)

        # 当前插件进度区
        plugin_box = QGroupBox("当前插件")
        plugin_layout = QVBoxLayout(plugin_box)

        plugin_row = QHBoxLayout()
        plugin_lbl = QLabel("翻译进度:")
        plugin_lbl.setFixedWidth(52)
        self._plugin_progress_bar = QProgressBar()
        self._plugin_progress_bar.setRange(0, 100)
        self._plugin_progress_bar.setValue(0)
        self._plugin_progress_lbl = QLabel("0 / 0")
        self._plugin_progress_lbl.setFixedWidth(80)
        plugin_row.addWidget(plugin_lbl)
        plugin_row.addWidget(self._plugin_progress_bar)
        plugin_row.addWidget(self._plugin_progress_lbl)
        plugin_layout.addLayout(plugin_row)

        self._plugin_msg = QLabel("—")
        self._plugin_msg.setStyleSheet("font-size: 11px; color: #555;")
        plugin_layout.addWidget(self._plugin_msg)

        stats_row = QHBoxLayout()
        self._lbl_success = QLabel("成功: 0")
        self._lbl_success.setStyleSheet("color: #4CAF50; font-weight: bold;")
        self._lbl_failed = QLabel("失败: 0")
        self._lbl_failed.setStyleSheet("color: #F44336; font-weight: bold;")
        self._lbl_terms = QLabel("新增术语: 0")
        self._lbl_terms.setStyleSheet("color: #2196F3; font-weight: bold;")
        for lbl in (self._lbl_success, self._lbl_failed, self._lbl_terms):
            stats_row.addWidget(lbl)
        stats_row.addStretch()
        plugin_layout.addLayout(stats_row)

        layout.addWidget(plugin_box)

        # 日志区
        log_box = QGroupBox("详细日志")
        log_layout = QVBoxLayout(log_box)

        # 轮次级日志
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
        self._scroll.setMinimumHeight(180)
        log_layout.addWidget(self._scroll)

        layout.addWidget(log_box)

        # 按钮行
        btn_row = QHBoxLayout()
        self._pause_btn = QPushButton("⏸ 暂停")
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
        self._worker.plugin_started.connect(self._on_plugin_started)
        self._worker.plugin_finished.connect(self._on_plugin_finished)
        self._worker.plugin_progress.connect(self._on_plugin_progress)
        self._worker.log.connect(self._on_log)
        self._worker.all_finished.connect(self._on_all_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_worker_finished)

    # ── 槽 ────────────────────────────────────────────────────────────────────

    def _on_plugin_started(self, plugin_name: str, current: int, total: int):
        self._current_plugin_idx = current
        self._total_plugins = total

        # 更新总体进度
        if total > 0:
            self._overall_progress_bar.setMaximum(total)
            self._overall_progress_bar.setValue(current - 1)
            self._overall_progress_lbl.setText(f"{current - 1} / {total} 插件")
        self._overall_msg.setText(f"正在翻译: {plugin_name}")

        # 重置当前插件进度
        self._plugin_progress_bar.setValue(0)
        self._plugin_progress_lbl.setText("0 / 0")
        self._plugin_msg.setText(plugin_name)

        # 清空批次组件
        for w in self._batch_widgets.values():
            w.deleteLater()
        self._batch_widgets.clear()

        self._round_log.append(f"\n▶ 开始翻译: {plugin_name} ({current}/{total})")

    def _on_plugin_finished(self, plugin_name: str, result):
        # 更新总体进度
        if self._total_plugins > 0:
            self._overall_progress_bar.setValue(self._current_plugin_idx)
            self._overall_progress_lbl.setText(f"{self._current_plugin_idx} / {self._total_plugins} 插件")

        # 折叠所有批次组件
        for w in self._batch_widgets.values():
            w.force_collapse()

        if result:
            # 构建后处理摘要（如果有）
            pp_info = ""
            if hasattr(result, 'post_process_result') and result.post_process_result:
                pp = result.post_process_result
                error_count = sum(1 for i in pp.issues if i.severity == "error")
                warning_count = sum(1 for i in pp.issues if i.severity == "warning")
                if error_count > 0 or warning_count > 0:
                    pp_info = f"（质量检查：{error_count} 错误，{warning_count} 警告）"

            log_msg = f"✅ {plugin_name} 完成 — 成功 {result.success_count} 条，失败 {result.failed_count} 条"
            if pp_info:
                log_msg += f" {pp_info}"
            self._round_log.append(log_msg)
        else:
            self._round_log.append(f"⚠ {plugin_name} 翻译失败或被中断")

    def _on_plugin_progress(
        self, current: int, total: int, message: str,
        success: int, failed: int, new_terms: int
    ):
        if total > 0:
            self._plugin_progress_bar.setMaximum(total)
            self._plugin_progress_bar.setValue(current)
            self._plugin_progress_lbl.setText(f"{current} / {total}")
        self._plugin_msg.setText(message)
        self._lbl_success.setText(f"成功: {success}")
        self._lbl_failed.setText(f"失败: {failed}")
        self._lbl_terms.setText(f"新增术语: {new_terms}")

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
            count = self._batches_layout.count()
            self._batches_layout.insertWidget(count - 1, w)

        widget = self._batch_widgets[batch_idx]
        sb = self._scroll.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        widget.append_line(line)

        if at_bottom:
            QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            ))

    def _on_all_finished(self, summary: "BatchTranslationSummary"):
        self._overall_progress_bar.setMaximum(100)
        self._overall_progress_bar.setValue(100)
        self._overall_progress_lbl.setText("完成")

        # 折叠所有未完成的批次组件
        for w in self._batch_widgets.values():
            w.force_collapse()

        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._llm_log_btn.setEnabled(False)

        # 停止日志查看器自动刷新
        if self._log_viewer is not None:
            self._log_viewer.stop_auto_refresh()

        self._round_log.append(
            f"\n{'⏹' if self._was_stopped else '✅'} 批量翻译{'已停止' if self._was_stopped else '完成'}"
        )

        self._overall_msg.setText(
            f"完成 {summary.success_plugins}/{summary.total_plugins} 个插件，"
            f"共 {summary.total_success_entries} 条成功"
        )

        # 通知集合更新
        for slot in self._ctx.slots.values():
            if slot.collection:
                self._ctx.collection_changed.emit(slot.collection)
                break  # 只发一次信号

        self.translation_completed.emit()

        # ── 为每个插件生成独立报告 ──
        plugin_results = []
        for d in summary.details:
            esp_stem = d.plugin_name
            report_path = None
            needs_review = 0
            if d.success and d.result:
                try:
                    from src.transbridge.ai_translator.post_processor.report_generator import ReportGenerator
                    gen = ReportGenerator(esp_stem)
                    report_path = gen.generate_translate_report(
                        d.result,
                        refine_results=getattr(d.result, 'refine_results', None),
                        polish_results=getattr(d.result, 'polish_results', None),
                        decisions=getattr(d.result, 'decisions', None),
                    )
                    d.result.report_path = report_path
                except Exception:
                    pass
                needs_review = len(d.result.post_process_result.needs_review) if (
                    d.result.post_process_result and d.result.post_process_result.needs_review
                ) else 0

            status = "success" if d.success else "failed"
            plugin_results.append({
                "esp_stem": esp_stem,
                "status": status,
                "success": d.result.success_count if d.result else 0,
                "failed": d.result.failed_count if d.result else 0,
                "skipped": d.result.skipped_count if d.result else 0,
                "needs_review": needs_review,
                "report_path": report_path,
                "result": d.result,
            })

        # ── 弹出批量汇总对话框（替代 QMessageBox）──
        if not self._background_mode and plugin_results:
            from ._batch_report_summary_dialog import _BatchReportSummaryDialog
            summary_dialog = _BatchReportSummaryDialog(plugin_results, parent=self)
            summary_dialog.open_plugin_report.connect(
                lambda idx: self._show_plugin_report(plugin_results[idx])
            )
            summary_dialog.show()

    def _show_plugin_report(self, plugin_info: dict):
        """从批量汇总中打开单个插件的报告对话框。"""
        result = plugin_info.get("result")
        report_path = plugin_info.get("report_path")
        if not result:
            return
        from ._translation_report_dialog import _TranslationReportDialog
        dialog = _TranslationReportDialog(
            translate_result=result,
            refine_results=getattr(result, 'refine_results', None),
            polish_results=getattr(result, 'polish_results', None),
            decisions=getattr(result, 'decisions', None),
            report_path=report_path,
        )
        main_win = self._find_main_window()
        if main_win and hasattr(main_win, '_on_report_entry_activated'):
            dialog.entry_activated.connect(main_win._on_report_entry_activated)
        dialog.show()

    @staticmethod
    def _find_main_window():
        """向上查找 MainWindow。"""
        from src.transbridge.ui.main_window import MainWindow
        for widget in QWidget.topLevelWidgets():
            if isinstance(widget, MainWindow):
                return widget
        return None

    def _on_error(self, err: str):
        for w in self._batch_widgets.values():
            w.force_collapse()
        self._overall_msg.setText(f"错误: {err}")
        self._round_log.append(f"\n❌ 全局错误: {err}")
        QMessageBox.critical(self, "批量翻译错误", err)

    def _on_open_log_viewer(self):
        """打开/激活 LLM 流式日志查看窗口。"""
        from src.transbridge.ui.tools.ai_translator._batch_llm_log_viewer import _BatchLLMLogViewer
        path = self._worker.stream_log_dir
        if not path:
            return
        if self._log_viewer is None or not self._log_viewer.isVisible():
            self._log_viewer = _BatchLLMLogViewer(path, parent=None)
        self._log_viewer.show()
        self._log_viewer.raise_()

    def _on_worker_finished(self):
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._llm_log_btn.setEnabled(False)
        if self._log_viewer is not None:
            self._log_viewer.stop_auto_refresh()

    def _on_pause_resume(self):
        if self._worker.is_paused:
            self._worker.resume()
            self._pause_btn.setText("⏸ 暂停")
            self._overall_msg.setText("已继续")
            self._round_log.append("▶ 已继续")
        else:
            self._worker.pause()
            self._pause_btn.setText("▶ 继续")
            self._overall_msg.setText("⏸ 已暂停")
            self._round_log.append("⏸ 已暂停")

    def _on_stop(self):
        reply = QMessageBox.question(
            self, "停止批量翻译",
            "确定要停止批量翻译吗？\n"
            "当前插件的翻译进度将保存，已翻译的内容不会丢失。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._was_stopped = True
            self._overall_msg.setText("⏹ 正在停止…")
            self._round_log.append("⏹ 已请求停止")
            self._worker.stop()
            self._stop_btn.setEnabled(False)
            self._pause_btn.setEnabled(False)

    # ── 关闭事件 ──────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._worker.isRunning()

    def closeEvent(self, event):
        if not self._worker.isRunning():
            event.accept()
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("批量翻译仍在进行中")
        dlg_layout = QVBoxLayout(dlg)
        dlg_layout.addWidget(QLabel("批量翻译仍在进行中，请选择操作："))

        bg = QButtonGroup(dlg)
        rb_stop = QRadioButton("停止翻译并关闭")
        rb_bg = QRadioButton("后台继续，关闭窗口")
        rb_stop.setChecked(True)
        bg.addButton(rb_stop)
        bg.addButton(rb_bg)
        dlg_layout.addWidget(rb_stop)
        dlg_layout.addWidget(rb_bg)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            event.ignore()
            return

        if rb_stop.isChecked():
            self._worker.stop()
            self._worker.wait(3000)
            event.accept()
        else:
            self._background_mode = True
            event.accept()
