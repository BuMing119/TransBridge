"""
AI 翻译浮窗（双窗口架构）。

AITranslatorWindow      — 配置窗口，翻译开始前使用
_TranslationProgressWindow — 进度窗口，翻译进行中使用
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QLineEdit, QComboBox, QSpinBox, QPushButton, QProgressBar,
    QRadioButton, QButtonGroup, QFileDialog, QMessageBox,
    QCheckBox, QListWidget, QListWidgetItem, QDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTextEdit,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QBrush

if TYPE_CHECKING:
    from src.transbridge.ui.context import AppContext
    from src.transbridge.ui.workbench.step2 import Step2PreviewWidget


# ─────────────────────────── TranslationWorker ───────────────────────────────

class _TranslationWorker(QThread):
    # current, total, message, success, failed, new_terms
    progress = pyqtSignal(int, int, str, int, int, int)
    log = pyqtSignal(str)       # 详细日志行（每条译文 / API 错误）
    result = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, translator, collection, target_entries, checkpoint=None):
        super().__init__()
        self._translator = translator
        self._collection = collection
        self._target_entries = target_entries
        self._checkpoint = checkpoint
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()   # 初始为运行状态

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()   # 确保 wait() 不阻塞

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    def run(self):
        try:
            result = self._translator.translate(
                collection=self._collection,
                target_entry_ids=self._target_entries,
                progress_callback=lambda c, t, m, s, f, n: self.progress.emit(c, t, m, s, f, n),
                stop_event=self._stop_event,
                pause_event=self._pause_event,
                checkpoint=self._checkpoint,
                log_callback=lambda line: self.log.emit(line),
            )
            self.result.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ─────────────────────────── 动态术语库编辑对话框 ─────────────────────────────

class _TermEditorDialog(QDialog):
    def __init__(self, dynamic_db, parent=None):
        super().__init__(parent)
        self._db = dynamic_db
        self.setWindowTitle("编辑动态术语库")
        self.resize(600, 400)
        self._init_ui()
        self._load_terms()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["原词", "译词", "来源", "上下文"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _load_terms(self):
        entries = self._db.as_list()
        self._table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            self._table.setItem(row, 0, QTableWidgetItem(e.term))
            self._table.setItem(row, 1, QTableWidgetItem(e.translation))
            self._table.setItem(row, 2, QTableWidgetItem(e.source))
            self._table.setItem(row, 3, QTableWidgetItem(e.context))


# ─────────────────────────── 进度窗口 ────────────────────────────────────────

class _TranslationProgressWindow(QWidget):
    """翻译进行中的进度窗口，支持暂停/继续/后台/停止。"""

    translation_completed = pyqtSignal()

    def __init__(self, worker: _TranslationWorker, ctx: "AppContext", parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._worker = worker
        self._ctx = ctx
        self.setWindowTitle("AI 自动翻译 — 进行中")
        self.resize(560, 480)
        self._background_mode = False   # True = 用户选择后台继续
        self._collection_synced = False  # 避免 result/error + finished 双重 emit
        self._was_stopped = False        # 用户是否主动停止
        self._init_ui()
        self._connect_worker()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 进度区
        prog_box = QGroupBox("翻译进度")
        prog_layout = QVBoxLayout(prog_box)

        # 总进度条（词条数）
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
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        self._log.setMinimumHeight(200)
        log_layout.addWidget(self._log)
        layout.addWidget(log_box)

        # 按钮行
        btn_row = QHBoxLayout()
        self._pause_btn = QPushButton("⏸ 暂停")
        self._pause_btn.clicked.connect(self._on_pause_resume)
        self._stop_btn = QPushButton("⏹ 停止")
        self._stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self._pause_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
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
        # 批次开始时（current 为批次序号，message 不含 [已跳过]）打印批次标题
        if not message.endswith("]"):
            self._log.append(f"\n▶ {message}")
            self._log.verticalScrollBar().setValue(
                self._log.verticalScrollBar().maximum()
            )

    def _on_log(self, line: str):
        self._log.append(line)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    def _on_result(self, result):
        self._total_progress_bar.setMaximum(100)
        self._total_progress_bar.setValue(100)
        self._total_progress_lbl.setText("完成")

        if self._was_stopped:
            self._progress_msg.setText("已停止")
            self._lbl_success.setText(f"成功: {result.success_count}")
            self._lbl_failed.setText(f"失败: {result.failed_count}")
            self._lbl_terms.setText(f"新增术语: {result.new_dynamic_terms}")
            self._log.append(
                f"\n⏹ 已停止 — 成功 {result.success_count} 条，"
                f"失败 {result.failed_count} 条，新增术语 {result.new_dynamic_terms} 个"
            )
        else:
            self._progress_msg.setText("翻译完成")
            self._lbl_success.setText(f"成功: {result.success_count}")
            self._lbl_failed.setText(f"失败: {result.failed_count}")
            self._lbl_terms.setText(f"新增术语: {result.new_dynamic_terms}")
            self._log.append(
                f"\n✅ 完成 — 成功 {result.success_count} 条，"
                f"失败 {result.failed_count} 条，新增术语 {result.new_dynamic_terms} 个"
            )

        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        self._collection_synced = True
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
            QMessageBox.information(
                self, "翻译完成",
                f"成功：{result.success_count} 条\n"
                f"失败：{result.failed_count} 条\n"
                f"跳过：{result.skipped_count} 条\n"
                f"新增术语：{result.new_dynamic_terms} 个",
            )

    def _on_error(self, err: str):
        self._progress_msg.setText(f"错误: {err}")
        self._log.append(f"\n❌ 全局错误: {err}")
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())
        self._collection_synced = True
        self._ctx.collection_changed.emit(self._ctx.collection)
        QMessageBox.critical(self, "翻译错误", err)

    def _on_worker_finished(self):
        self._pause_btn.setEnabled(False)
        self._stop_btn.setEnabled(False)
        # 若 result/error 未触发（例如用户停止），补发 collection_changed 以同步已完成的批次
        if not self._collection_synced:
            self._collection_synced = True
            self._ctx.collection_changed.emit(self._ctx.collection)

    def _on_pause_resume(self):
        if self._worker.is_paused:
            self._worker.resume()
            self._pause_btn.setText("⏸ 暂停")
            self._progress_msg.setText("已继续 - 等待下一批")
            self._log.append("▶ 已继续")
        else:
            self._worker.pause()
            self._pause_btn.setText("▶ 继续")
            self._progress_msg.setText("⏸ 已暂停（当前 API 调用将立即中断）")
            self._log.append("⏸ 已暂停")
            # 暂停时立即同步已完成批次到 UI
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
            self._log.append("⏹ 已请求停止")
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

        from PyQt6.QtWidgets import QDialog, QDialogButtonBox
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
            # 后台继续
            self._background_mode = True
            event.accept()


# ─────────────────────────── 配置窗口 ────────────────────────────────────────

class AITranslatorWindow(QWidget):
    """AI 翻译配置窗口。点击「开始翻译」后关闭自身并弹出进度窗口。"""

    # 翻译启动后发出，携带进度窗口实例
    progress_window_created = pyqtSignal(object)

    def __init__(self, ctx: "AppContext", step2: "Step2PreviewWidget", parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._ctx = ctx
        self._step2 = step2
        self.setWindowTitle("AI 自动翻译")
        self.resize(560, 680)
        self._init_ui()
        self._load_config()
        self._check_checkpoint()

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)

        # ── LLM 配置区 ────────────────────────────────────────────────────────
        llm_box = QGroupBox("LLM 配置")
        llm_layout = QVBoxLayout(llm_box)
        llm_layout.setSpacing(4)

        def _row(label_text, widget):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(90)
            row.addWidget(lbl)
            row.addWidget(widget)
            return row

        self._provider_combo = QComboBox()
        self._provider_combo.addItems(["OpenAI 兼容", "Anthropic"])
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        llm_layout.addLayout(_row("供应商:", self._provider_combo))

        self._model_edit = QLineEdit()
        self._model_edit.setPlaceholderText("如 gpt-4o / deepseek-chat")
        llm_layout.addLayout(_row("模型名:", self._model_edit))

        self._apikey_edit = QLineEdit()
        self._apikey_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._apikey_edit.setPlaceholderText("API Key")
        llm_layout.addLayout(_row("API Key:", self._apikey_edit))

        self._baseurl_edit = QLineEdit()
        self._baseurl_edit.setPlaceholderText("https://api.openai.com/v1")
        llm_layout.addLayout(_row("Base URL:", self._baseurl_edit))

        self._concurrent_spin = QSpinBox()
        self._concurrent_spin.setRange(1, 20)
        self._concurrent_spin.setValue(3)
        llm_layout.addLayout(_row("并发数:", self._concurrent_spin))

        self._tokens_spin = QSpinBox()
        self._tokens_spin.setRange(200, 32000)
        self._tokens_spin.setSingleStep(200)
        self._tokens_spin.setValue(2000)
        llm_layout.addLayout(_row("拆批 Token:", self._tokens_spin))

        self._output_tokens_spin = QSpinBox()
        self._output_tokens_spin.setRange(0, 65536)
        self._output_tokens_spin.setSingleStep(256)
        self._output_tokens_spin.setValue(0)
        self._output_tokens_spin.setSpecialValueText("不限制（模型默认）")
        llm_layout.addLayout(_row("输出 Token:", self._output_tokens_spin))

        test_btn = QPushButton("测试连接")
        test_btn.setFixedWidth(100)
        test_btn.clicked.connect(self._on_test_connection)
        test_row = QHBoxLayout()
        test_row.addStretch()
        test_row.addWidget(test_btn)
        llm_layout.addLayout(test_row)

        main_layout.addWidget(llm_box)

        # ── 术语库配置区 ──────────────────────────────────────────────────────
        term_box = QGroupBox("术语库来源（上方优先级更高）")
        term_layout = QVBoxLayout(term_box)

        self._priority_list = QListWidget()
        self._priority_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._priority_list.setMaximumHeight(110)
        for source_name in ["dynamic（动态词库）", "paratranz（ParaTranz 术语）",
                             "json（本地 JSON）", "excel（本地 Excel）"]:
            self._priority_list.addItem(QListWidgetItem(source_name))
        term_layout.addWidget(self._priority_list)

        json_row = QHBoxLayout()
        json_row.addWidget(QLabel("本地 JSON:"))
        self._json_path_edit = QLineEdit()
        self._json_path_edit.setPlaceholderText("可选")
        json_row.addWidget(self._json_path_edit)
        json_browse = QPushButton("浏览")
        json_browse.setFixedWidth(52)
        json_browse.clicked.connect(lambda: self._browse_file(self._json_path_edit, "JSON 文件 (*.json)"))
        json_row.addWidget(json_browse)
        term_layout.addLayout(json_row)

        excel_row = QHBoxLayout()
        excel_row.addWidget(QLabel("本地 Excel:"))
        self._excel_path_edit = QLineEdit()
        self._excel_path_edit.setPlaceholderText("可选")
        excel_row.addWidget(self._excel_path_edit)
        excel_browse = QPushButton("浏览")
        excel_browse.setFixedWidth(52)
        excel_browse.clicked.connect(lambda: self._browse_file(self._excel_path_edit, "Excel 文件 (*.xlsx *.xls)"))
        excel_row.addWidget(excel_browse)
        term_layout.addLayout(excel_row)

        excel_col_row = QHBoxLayout()
        excel_col_row.addWidget(QLabel("原文列:"))
        self._excel_orig_col_edit = QLineEdit("A")
        self._excel_orig_col_edit.setFixedWidth(40)
        excel_col_row.addWidget(self._excel_orig_col_edit)
        excel_col_row.addWidget(QLabel("译文列:"))
        self._excel_trans_col_edit = QLineEdit("B")
        self._excel_trans_col_edit.setFixedWidth(40)
        excel_col_row.addWidget(self._excel_trans_col_edit)
        excel_col_row.addStretch()
        term_layout.addLayout(excel_col_row)

        view_terms_btn = QPushButton("查看/编辑动态术语库")
        view_terms_btn.clicked.connect(self._on_view_terms)
        term_layout.addWidget(view_terms_btn)

        main_layout.addWidget(term_box)

        # ── 翻译范围区 ────────────────────────────────────────────────────────
        scope_box = QGroupBox("翻译范围")
        scope_layout = QVBoxLayout(scope_box)

        self._scope_group = QButtonGroup(self)
        self._scope_all = QRadioButton("翻译集合中所有未翻译词条")
        self._scope_filtered = QRadioButton("翻译当前筛选可见词条（仅未翻译）")
        self._scope_selected = QRadioButton("翻译选中词条（0 条）")
        self._scope_all.setChecked(True)

        for rb in (self._scope_all, self._scope_filtered, self._scope_selected):
            self._scope_group.addButton(rb)
            scope_layout.addWidget(rb)

        self._overwrite_check = QCheckBox("覆盖已有译文（重新翻译）")
        scope_layout.addWidget(self._overwrite_check)

        self._estimate_lbl = QLabel("预计：— 条")
        self._estimate_lbl.setStyleSheet("color: #888; font-size: 11px;")
        scope_layout.addWidget(self._estimate_lbl)

        main_layout.addWidget(scope_box)

        # ── 底部按钮 ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._start_btn = QPushButton("▶ 开始翻译")
        self._start_btn.setStyleSheet(
            "QPushButton { background: #1976D2; color: white; font-weight: bold;"
            " padding: 6px 16px; border-radius: 4px; }"
            "QPushButton:hover { background: #1565C0; }"
            "QPushButton:disabled { background: #bbb; }"
        )
        self._start_btn.clicked.connect(self._on_start)
        btn_row.addWidget(self._start_btn)
        main_layout.addLayout(btn_row)

        self._scope_selected.toggled.connect(self._update_estimate)
        self._scope_all.toggled.connect(self._update_estimate)
        self._scope_filtered.toggled.connect(self._update_estimate)
        self._overwrite_check.toggled.connect(self._update_estimate)

    # ── 断点检测 ──────────────────────────────────────────────────────────────

    def _check_checkpoint(self):
        esp_path = self._ctx.esp_path
        if not esp_path:
            return
        from src.transbridge.ai_translator.translator import ProgressCheckpoint
        cp = ProgressCheckpoint.load(esp_path)
        if cp is None:
            return
        done = len(cp.completed_fingerprints)
        reply = QMessageBox.question(
            self, "检测到未完成的翻译任务",
            f"上次翻译未完成（已完成 {done} 批，"
            f"成功 {cp.result_so_far.get('success_count', 0)} 条）。\n\n"
            "是否从断点继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.No:
            cp.delete(esp_path)

    # ── 配置加载/保存 ─────────────────────────────────────────────────────────

    def _load_config(self):
        from src.transbridge.paratranz.config_manager import LLMConfig
        cfg = LLMConfig.load_from_file()
        self._provider_combo.setCurrentIndex(0 if cfg.provider != "anthropic" else 1)
        self._model_edit.setText(cfg.model)
        self._apikey_edit.setText(cfg.api_key)
        self._baseurl_edit.setText(cfg.base_url)
        self._concurrent_spin.setValue(cfg.max_concurrent)
        self._tokens_spin.setValue(cfg.max_tokens_per_batch)
        self._output_tokens_spin.setValue(cfg.max_output_tokens)
        self._json_path_edit.setText(cfg.local_json_path)
        self._excel_path_edit.setText(cfg.local_excel_path)
        self._excel_orig_col_edit.setText(cfg.excel_original_col)
        self._excel_trans_col_edit.setText(cfg.excel_translation_col)
        priority_map = {
            "dynamic": "dynamic（动态词库）",
            "paratranz": "paratranz（ParaTranz 术语）",
            "json": "json（本地 JSON）",
            "excel": "excel（本地 Excel）",
        }
        if cfg.term_priority:
            self._priority_list.clear()
            for p in cfg.term_priority:
                if p in priority_map:
                    self._priority_list.addItem(QListWidgetItem(priority_map[p]))
        self._on_provider_changed()
        self._update_estimate()
        self._update_paratranz_item_style()

    def _update_paratranz_item_style(self):
        """若未选择 ParaTranz 项目，将列表中 paratranz 来源项显示为灰色并加提示。"""
        no_project = not self._ctx.current_project
        for i in range(self._priority_list.count()):
            item = self._priority_list.item(i)
            if "paratranz" in item.text():
                if no_project:
                    item.setForeground(QBrush(QColor("#999999")))
                    item.setToolTip("未选择 ParaTranz 项目，此来源将被跳过")
                else:
                    item.setForeground(QBrush(QColor()))  # 恢复默认颜色
                    item.setToolTip("")
                break

    def _save_config(self):
        from src.transbridge.paratranz.config_manager import LLMConfig
        cfg = LLMConfig.load_from_file()
        cfg.provider = "anthropic" if self._provider_combo.currentIndex() == 1 else "openai_compatible"
        cfg.model = self._model_edit.text().strip()
        cfg.api_key = self._apikey_edit.text().strip()
        cfg.base_url = self._baseurl_edit.text().strip()
        cfg.max_concurrent = self._concurrent_spin.value()
        cfg.max_tokens_per_batch = self._tokens_spin.value()
        cfg.max_output_tokens = self._output_tokens_spin.value()
        cfg.local_json_path = self._json_path_edit.text().strip()
        cfg.local_excel_path = self._excel_path_edit.text().strip()
        cfg.excel_original_col = self._excel_orig_col_edit.text().strip() or "A"
        cfg.excel_translation_col = self._excel_trans_col_edit.text().strip() or "B"
        key_map = {
            "dynamic（动态词库）": "dynamic",
            "paratranz（ParaTranz 术语）": "paratranz",
            "json（本地 JSON）": "json",
            "excel（本地 Excel）": "excel",
        }
        cfg.term_priority = [
            key_map[self._priority_list.item(i).text()]
            for i in range(self._priority_list.count())
            if self._priority_list.item(i).text() in key_map
        ]
        cfg.save_to_file()
        return cfg

    def _build_llm_config(self):
        from src.transbridge.paratranz.config_manager import LLMConfig
        cfg = LLMConfig()
        cfg.provider = "anthropic" if self._provider_combo.currentIndex() == 1 else "openai_compatible"
        cfg.model = self._model_edit.text().strip()
        cfg.api_key = self._apikey_edit.text().strip()
        cfg.base_url = self._baseurl_edit.text().strip() or "https://api.openai.com/v1"
        cfg.max_concurrent = self._concurrent_spin.value()
        cfg.max_tokens_per_batch = self._tokens_spin.value()
        cfg.max_output_tokens = self._output_tokens_spin.value()
        cfg.local_json_path = self._json_path_edit.text().strip()
        cfg.local_excel_path = self._excel_path_edit.text().strip()
        cfg.excel_original_col = self._excel_orig_col_edit.text().strip() or "A"
        cfg.excel_translation_col = self._excel_trans_col_edit.text().strip() or "B"
        key_map = {
            "dynamic（动态词库）": "dynamic",
            "paratranz（ParaTranz 术语）": "paratranz",
            "json（本地 JSON）": "json",
            "excel（本地 Excel）": "excel",
        }
        cfg.term_priority = [
            key_map[self._priority_list.item(i).text()]
            for i in range(self._priority_list.count())
            if self._priority_list.item(i).text() in key_map
        ]
        return cfg

    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_provider_changed(self):
        is_openai = self._provider_combo.currentIndex() == 0
        self._baseurl_edit.setEnabled(is_openai)

    def _update_estimate(self):
        collection = self._ctx.collection
        if collection is None:
            self._scope_selected.setText("翻译选中词条（0 条）")
            self._estimate_lbl.setText("预计：— 条（需先加载集合）")
            return

        selected_count = len(self._step2.get_selected_entries())
        self._scope_selected.setText(f"翻译选中词条（{selected_count} 条）")

        overwrite = self._overwrite_check.isChecked()

        if self._scope_all.isChecked():
            candidates = list(collection) if overwrite else [
                e for e in collection if not e.translation or e.stage == 0
            ]
        elif self._scope_filtered.isChecked():
            self._estimate_lbl.setText(
                f"预计：约 {self._step2.get_filtered_count()} 条（筛选可见）"
            )
            return
        else:
            candidates = self._step2.get_selected_entries()
            if not overwrite:
                candidates = [e for e in candidates if not e.translation or e.stage == 0]

        from src.transbridge.ai_translator.batch_planner import BatchPlanner
        planner = BatchPlanner(max_tokens_per_batch=self._tokens_spin.value())
        plan = planner.plan(candidates)
        self._estimate_lbl.setText(
            f"预计：{plan.total_entries()} 条"
            f"（第一轮: {sum(len(b.entries) for b in plan.round1)}"
            f"  第二轮: {sum(len(b.entries) for b in plan.round2)}"
            f"  第三轮: {sum(len(b.entries) for b in plan.round3)}）"
        )

    def _on_test_connection(self):
        cfg = self._build_llm_config()
        if not cfg.api_key:
            QMessageBox.warning(self, "测试连接", "请先填写 API Key。")
            return
        if not cfg.model:
            QMessageBox.warning(self, "测试连接", "请先填写模型名。")
            return
        try:
            from src.transbridge.ai_translator.llm_client import create_llm_client
            client = create_llm_client(cfg)
            reply = client.chat([{"role": "user", "content": "Say 'OK' in one word."}], max_tokens=10)
            QMessageBox.information(self, "测试连接", f"连接成功！模型回复：{reply}")
        except Exception as exc:
            QMessageBox.critical(self, "测试连接失败", str(exc))

    def _browse_file(self, target_edit: QLineEdit, file_filter: str):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", file_filter)
        if path:
            target_edit.setText(path)

    def _on_view_terms(self):
        esp_path = self._ctx.esp_path
        if not esp_path:
            QMessageBox.warning(self, "术语库", "尚未加载 ESP 文件。")
            return
        from src.transbridge.ai_translator.term_database import DynamicTermDatabase
        db = DynamicTermDatabase(esp_path)
        db.load()
        dlg = _TermEditorDialog(db, parent=self)
        dlg.exec()

    def _on_start(self):
        collection = self._ctx.collection
        if not collection:
            QMessageBox.warning(self, "翻译", "请先加载词条集合。")
            return

        cfg = self._save_config()

        if not cfg.api_key:
            QMessageBox.warning(self, "翻译", "请先填写 API Key。")
            return
        if not cfg.model:
            QMessageBox.warning(self, "翻译", "请先填写模型名。")
            return
        if not self._ctx.esp_path:
            QMessageBox.warning(self, "翻译", "找不到 ESP 路径，请重新加载集合。")
            return

        # 检查是否无项目且术语库为空
        if not self._ctx.current_project and self._check_all_terms_empty(cfg):
            reply = QMessageBox.question(
                self, "术语库为空",
                "当前未选择 ParaTranz 项目，且所有术语来源均为空。\n\n"
                "没有术语库辅助，翻译质量可能下降。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # 确定翻译目标
        if self._scope_selected.isChecked():
            selected = self._step2.get_selected_entries()
            if not selected:
                QMessageBox.warning(self, "翻译", "未勾选任何词条。")
                return
            target_ids = [e.id for e in selected]
        elif self._scope_filtered.isChecked():
            target_ids = self._get_filtered_entry_ids()
        else:
            target_ids = None

        # 加载断点（如有）
        from src.transbridge.ai_translator.translator import AutoTranslator, TranslatorConfig, ProgressCheckpoint
        checkpoint = ProgressCheckpoint.load(self._ctx.esp_path)

        translator_cfg = TranslatorConfig(
            llm_config=cfg,
            esp_path=self._ctx.esp_path,
            overwrite=self._overwrite_check.isChecked(),
        )

        paratranz_client = None
        project_id = None
        if self._ctx.current_project:
            project_id = self._ctx.current_project.get("id")
            from src.transbridge.paratranz.api.paratranz_terms_api import ParatranzTermsAPI
            paratranz_client = ParatranzTermsAPI(self._ctx.config)

        translator = AutoTranslator(translator_cfg, paratranz_client, project_id)
        worker = _TranslationWorker(translator, collection, target_ids, checkpoint)

        # 创建进度窗口
        progress_win = _TranslationProgressWindow(worker, self._ctx)
        self.progress_window_created.emit(progress_win)

        # 关闭配置窗口，显示进度窗口
        progress_win.show()
        worker.start()
        self.close()

    def _check_all_terms_empty(self, cfg) -> bool:
        """检查所有术语来源是否均为空。"""
        import os
        # 检查动态术语库
        from src.transbridge.ai_translator.term_database import DynamicTermDatabase
        dynamic_db = DynamicTermDatabase(self._ctx.esp_path)
        dynamic_db.load()
        if dynamic_db.as_list():
            return False

        # 检查本地 JSON
        if cfg.local_json_path and os.path.exists(cfg.local_json_path):
            try:
                import json
                with open(cfg.local_json_path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list) and data:
                    return False
                if isinstance(data, dict) and data:
                    return False
            except Exception:
                pass

        # 检查本地 Excel
        if cfg.local_excel_path and os.path.exists(cfg.local_excel_path):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(cfg.local_excel_path, read_only=True, data_only=True)
                ws = wb.active
                col_orig = self._col_letter_to_index(cfg.excel_original_col or "A")
                col_trans = self._col_letter_to_index(cfg.excel_translation_col or "B")
                for row in ws.iter_rows(min_row=2, values_only=True):
                    try:
                        term = row[col_orig] if col_orig < len(row) else None
                        trans = row[col_trans] if col_trans < len(row) else None
                        if term and trans:
                            return False
                    except (IndexError, TypeError):
                        continue
            except Exception:
                pass

        return True

    @staticmethod
    def _col_letter_to_index(letter: str) -> int:
        """将列字母（A/B/AA 等）转换为 0 起始的列索引。"""
        letter = letter.upper().strip()
        idx = 0
        for ch in letter:
            idx = idx * 26 + (ord(ch) - ord("A") + 1)
        return idx - 1

    def _get_filtered_entry_ids(self) -> list[str] | None:
        from src.transbridge.ui.workbench.step2 import _COL_KEY
        result = []
        table = self._step2._table
        for row in range(table.rowCount()):
            if not table.isRowHidden(row):
                item = table.item(row, _COL_KEY)
                if item:
                    from src.transbridge.converter.translation_entry import TranslationEntry
                    e = item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(e, TranslationEntry):
                        result.append(e.id)
        return result if result else None
