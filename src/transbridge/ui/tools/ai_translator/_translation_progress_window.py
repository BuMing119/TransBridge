"""AI 翻译进度窗口，支持暂停/继续/停止/后台运行。"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QProgressBar, QPushButton, QRadioButton, QButtonGroup,
    QMessageBox, QDialog, QDialogButtonBox, QTextEdit,
    QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor

if TYPE_CHECKING:
    from src.transbridge.ui.context import AppContext
    from src.transbridge.ui.tools.ai_translator._translation_worker import _TranslationWorker


class _BatchWidget(QFrame):
    """单个批次的日志子组件。完成后自动折叠为单行摘要。"""

    def __init__(self, batch_idx: int, parent=None):
        super().__init__(parent)
        self._batch_idx = batch_idx
        self._phase = 'init'   # 'init' | 'header' | 'trans' | 'footer' | 'done'
        self._title = f'任务{batch_idx}'
        self._footer_lines: list[str] = []
        self._trans_cursors: deque = deque()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(2)

        self._header_label = QLabel(self._title)
        self._header_label.setStyleSheet("font-weight: bold; font-size: 11px;")
        layout.addWidget(self._header_label)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("Consolas", 8))
        self._text.setFixedHeight(160)
        layout.addWidget(self._text)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("QFrame { border: 1px solid #ddd; border-radius: 4px; margin: 2px; }")

    def append_line(self, line: str) -> None:
        stripped = line.strip()

        if '开始翻译：' in line:
            self._phase = 'header'
            return

        if self._phase == 'header':
            clean = line.lstrip('\n')
            cs = clean.strip()
            if cs.startswith('任务') and '：' in cs:
                self._title = cs
                self._header_label.setText(cs)
            elif cs == '-----------------------':
                self._phase = 'trans'
            return

        if self._phase == 'trans':
            if stripped == '-----------------------':
                self._phase = 'footer'
                return
            if ' -> ' in line:
                self._text.append(line)
                doc = self._text.document()
                block = doc.findBlockByNumber(doc.blockCount() - 1)
                cursor = QTextCursor(block)
                self._trans_cursors.append(cursor)
                if len(self._trans_cursors) > 10:
                    oldest = self._trans_cursors.popleft()
                    oldest.movePosition(QTextCursor.MoveOperation.StartOfBlock)
                    oldest.movePosition(
                        QTextCursor.MoveOperation.Down,
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    oldest.removeSelectedText()
            else:
                self._text.append(line)
            return

        if self._phase == 'footer':
            if stripped == '已完成：':
                self._footer_lines = []
                return
            self._footer_lines.append(stripped)
            self._text.append(line)
            if stripped.startswith('新增术语数：'):
                self._phase = 'done'
                self._collapse()
            return

        # 'init' or 'done': 直接追加
        self._text.append(line)

    def _collapse(self):
        """折叠为单行摘要。"""
        total_time = ''
        entries_count = ''
        new_terms = ''
        for fl in self._footer_lines:
            if fl.startswith('总时长：'):
                total_time = fl.replace('总时长：', '').strip()
            elif fl.startswith('翻译词条数：'):
                entries_count = fl.replace('翻译词条数：', '').strip()
            elif fl.startswith('新增术语数：'):
                new_terms = fl.replace('新增术语数：', '').strip()

        summary = f"✅ {self._title}"
        parts = []
        if entries_count:
            parts.append(f"{entries_count} 条")
        if total_time:
            parts.append(total_time)
        if new_terms and new_terms != '0':
            parts.append(f"新增术语 {new_terms}")
        if parts:
            summary += " — " + " | ".join(parts)

        self._header_label.setText(summary)
        self._text.hide()
        self.setStyleSheet(
            "QFrame { border: 1px solid #bdbdbd; border-radius: 4px; "
            "margin: 2px; background: #f5f5f5; }"
            "QLabel { color: #424242; }"
        )

    def force_collapse(self):
        """强制折叠（翻译被中断时调用）。"""
        if self._phase != 'done':
            self._header_label.setText(f"⚠ {self._title}（未完成）")
            self._text.hide()
            self._phase = 'done'
            self.setStyleSheet(
                "QFrame { border: 1px solid #ffe082; border-radius: 4px; "
                "margin: 2px; background: #fffde7; }"
            )


class _TranslationProgressWindow(QWidget):
    """翻译进行中的进度窗口，支持暂停/继续/后台/停止。"""

    translation_completed = pyqtSignal()

    def __init__(self, worker: "_TranslationWorker", ctx: "AppContext", parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._worker = worker
        self._ctx = ctx
        self.setWindowTitle("AI 自动翻译 — 进行中")
        self.resize(560, 600)
        self._background_mode = False
        self._collection_synced = False
        self._was_stopped = False
        self._log_viewer = None
        self._batch_widgets: dict[int, _BatchWidget] = {}
        self._init_ui()
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

        self._progress_msg = QLabel("准备中…")
        self._progress_msg.setStyleSheet("font-size: 11px; color: #555;")
        prog_layout.addWidget(self._progress_msg)

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

    def _on_progress(self, current: int, total: int, message: str,
                     success: int, failed: int, new_terms: int):
        if total > 0:
            self._total_progress_bar.setMaximum(total)
            self._total_progress_bar.setValue(current)
            self._total_progress_lbl.setText(f"{current} / {total}")
        self._progress_msg.setText(message)
        self._lbl_success.setText(f"成功: {success}")
        self._lbl_failed.setText(f"失败: {failed}")
        self._lbl_terms.setText(f"新增术语: {new_terms}")
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
            QTimer.singleShot(0, lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            ))

    def _on_open_log_viewer(self):
        """打开/激活 LLM 流式日志查看窗口。"""
        from src.transbridge.ui.tools.ai_translator._llm_log_viewer import _LLMLogViewer
        path = self._worker.stream_log_dir
        if not path:
            return
        if self._log_viewer is None or not self._log_viewer.isVisible():
            self._log_viewer = _LLMLogViewer(path, parent=None)
        self._log_viewer.show()
        self._log_viewer.raise_()

    def _on_result(self, result):
        self._total_progress_bar.setMaximum(100)
        self._total_progress_bar.setValue(100)
        self._total_progress_lbl.setText("完成")

        # 折叠所有未完成的批次组件
        for w in self._batch_widgets.values():
            w.force_collapse()

        # 构建后处理摘要（如果有）
        pp_summary = ""
        if hasattr(result, 'post_process_result') and result.post_process_result:
            pp = result.post_process_result
            error_count = sum(1 for i in pp.issues if i.severity == "error")
            warning_count = sum(1 for i in pp.issues if i.severity == "warning")
            pp_summary = f"\n\n质量检查：{pp.total_checked} 条"
            if error_count > 0 or warning_count > 0:
                pp_summary += f"（{error_count} 错误，{warning_count} 警告）"
            if pp.needs_review:
                pp_summary += f"\n需审核：{len(pp.needs_review)} 条"

        if self._was_stopped:
            self._progress_msg.setText("已停止")
            self._lbl_success.setText(f"成功: {result.success_count}")
            self._lbl_failed.setText(f"失败: {result.failed_count}")
            self._lbl_terms.setText(f"新增术语: {result.new_dynamic_terms}")
            self._round_log.append(
                f"\n⏹ 已停止 — 成功 {result.success_count} 条，"
                f"失败 {result.failed_count} 条，新增术语 {result.new_dynamic_terms} 个"
            )
        else:
            self._progress_msg.setText("翻译完成")
            self._lbl_success.setText(f"成功: {result.success_count}")
            self._lbl_failed.setText(f"失败: {result.failed_count}")
            self._lbl_terms.setText(f"新增术语: {result.new_dynamic_terms}")
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

        if self._was_stopped:
            QMessageBox.information(
                self, "翻译已停止",
                f"成功：{result.success_count} 条\n"
                f"失败：{result.failed_count} 条\n"
                f"跳过：{result.skipped_count} 条\n"
                f"新增术语：{result.new_dynamic_terms} 个\n\n"
                f"已保存断点，可通过断点续传继续。",
            )
        else:
            msg = (
                f"成功：{result.success_count} 条\n"
                f"失败：{result.failed_count} 条\n"
                f"跳过：{result.skipped_count} 条\n"
                f"新增术语：{result.new_dynamic_terms} 个"
            )
            if pp_summary:
                msg += pp_summary
            QMessageBox.information(self, "翻译完成", msg)

    def _on_error(self, err: str):
        for w in self._batch_widgets.values():
            w.force_collapse()
        self._progress_msg.setText(f"错误: {err}")
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
            self._pause_btn.setText("⏸ 暂停")
            self._progress_msg.setText("已继续 - 等待下一批")
            self._round_log.append("▶ 已继续")
        else:
            self._worker.pause()
            self._pause_btn.setText("▶ 继续")
            self._progress_msg.setText("⏸ 已暂停（当前 API 调用将立即中断）")
            self._round_log.append("⏸ 已暂停")
            self._ctx.collection_changed.emit(self._ctx.collection)

    def _on_stop(self):
        reply = QMessageBox.question(
            self, "停止翻译",
            "确定要停止翻译吗？\n已翻译的内容不会丢失，可通过断点续传继续。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._was_stopped = True
            self._progress_msg.setText("⏹ 正在停止（当前 API 调用将立即中断）")
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