"""
AI 翻译配置窗口。

AITranslatorWindow  — 配置窗口，翻译开始前使用
进度窗口见 _translation_progress_window.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel,
    QLineEdit, QComboBox, QSpinBox, QPushButton,
    QRadioButton, QButtonGroup, QFileDialog, QMessageBox,
    QCheckBox, QListWidget, QListWidgetItem,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush

from src.transbridge.ui.tools.ai_translator._translation_worker import _TranslationWorker
from src.transbridge.ui.tools.ai_translator._translation_progress_window import _TranslationProgressWindow
from src.transbridge.ui.tools.ai_translator._term_editor_dialog import _TermEditorDialog

if TYPE_CHECKING:
    from src.transbridge.ui.context import AppContext
    from src.transbridge.ui.workbench.step2 import Step2PreviewWidget


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
        self._connect_auto_save()
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
        self._concurrent_spin.setRange(1, 50)
        self._concurrent_spin.setValue(20)
        llm_layout.addLayout(_row("并发数:", self._concurrent_spin))

        self._tokens_spin = QSpinBox()
        self._tokens_spin.setRange(200, 32000)
        self._tokens_spin.setSingleStep(200)
        self._tokens_spin.setValue(2500)
        llm_layout.addLayout(_row("拆批 Token:", self._tokens_spin))

        self._output_tokens_spin = QSpinBox()
        self._output_tokens_spin.setRange(0, 65536)
        self._output_tokens_spin.setSingleStep(256)
        self._output_tokens_spin.setValue(0)
        self._output_tokens_spin.setSpecialValueText("不限制（模型默认）")
        llm_layout.addLayout(_row("输出 Token:", self._output_tokens_spin))

        self._max_terms_spin = QSpinBox()
        self._max_terms_spin.setRange(10, 500)
        self._max_terms_spin.setValue(50)
        self._max_terms_spin.setToolTip("每批次发送给 LLM 的术语表上限，防止 token 超限")
        llm_layout.addLayout(_row("术语上限:", self._max_terms_spin))

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
        self._max_terms_spin.setValue(cfg.max_terms_per_batch)
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
        cfg.max_terms_per_batch = self._max_terms_spin.value()
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

    def _connect_auto_save(self):
        """在配置加载完成后连接所有控件的变更信号，实现自动保存。"""
        self._provider_combo.currentIndexChanged.connect(self._save_config)
        self._model_edit.textChanged.connect(self._save_config)
        self._apikey_edit.textChanged.connect(self._save_config)
        self._baseurl_edit.textChanged.connect(self._save_config)
        self._concurrent_spin.valueChanged.connect(self._save_config)
        self._tokens_spin.valueChanged.connect(self._save_config)
        self._output_tokens_spin.valueChanged.connect(self._save_config)
        self._max_terms_spin.valueChanged.connect(self._save_config)
        self._json_path_edit.textChanged.connect(self._save_config)
        self._excel_path_edit.textChanged.connect(self._save_config)
        self._excel_orig_col_edit.textChanged.connect(self._save_config)
        self._excel_trans_col_edit.textChanged.connect(self._save_config)
        self._priority_list.model().rowsMoved.connect(self._save_config)

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
        cfg.max_terms_per_batch = self._max_terms_spin.value()
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

        progress_win = _TranslationProgressWindow(worker, self._ctx)
        self.progress_window_created.emit(progress_win)

        progress_win.show()
        worker.start()
        self.close()

    def _check_all_terms_empty(self, cfg) -> bool:
        """检查所有术语来源是否均为空。"""
        import os
        from src.transbridge.ai_translator.term_database import DynamicTermDatabase
        dynamic_db = DynamicTermDatabase(self._ctx.esp_path)
        dynamic_db.load()
        if dynamic_db.as_list():
            return False

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