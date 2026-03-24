"""
步骤1：源文件选择与解析。
选择 ESP/ESM 插件文件（必填）以及可选的 EET/XT XML 文件，点击解析后在后台执行。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QPushButton, QFileDialog, QComboBox, QProgressBar, QLabel,
)
from PyQt6.QtCore import pyqtSignal, Qt

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.parser.plugin_parser import PluginParser
from src.transbridge.parser.xt_parser import XT_XmlParser
from ..workers import ApiWorker


class Step1SourceWidget(QWidget):
    """源文件选择与解析面板。解析完成后通过 ctx.collection 广播结果。"""

    parse_started = pyqtSignal()
    parse_finished = pyqtSignal(object)  # TranslationEntryCollection | None

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._init_ui()

    def _init_ui(self):
        box = QGroupBox("步骤1：源文件选择")
        outer = QVBoxLayout(self)
        outer.addWidget(box)
        outer.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(box)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 插件文件
        esp_row = QHBoxLayout()
        self._esp_input = QLineEdit()
        self._esp_input.setPlaceholderText("选择 .esp / .esm / .esl 文件…")
        self._esp_input.setReadOnly(True)
        esp_btn = QPushButton("浏览")
        esp_btn.setFixedWidth(60)
        esp_btn.clicked.connect(self._browse_esp)
        esp_row.addWidget(self._esp_input)
        esp_row.addWidget(esp_btn)
        form.addRow("插件文件 *", esp_row)

        # EET XML（可选）
        eet_row = QHBoxLayout()
        self._eet_input = QLineEdit()
        self._eet_input.setPlaceholderText("可选，迁移旧 EET 译文")
        self._eet_input.setReadOnly(True)
        eet_btn = QPushButton("浏览")
        eet_btn.setFixedWidth(60)
        eet_btn.clicked.connect(self._browse_eet)
        eet_row.addWidget(self._eet_input)
        eet_row.addWidget(eet_btn)
        form.addRow("EET XML", eet_row)

        # XT XML（可选）
        xt_row = QHBoxLayout()
        self._xt_input = QLineEdit()
        self._xt_input.setPlaceholderText("可选，迁移旧 XT 译文")
        self._xt_input.setReadOnly(True)
        xt_btn = QPushButton("浏览")
        xt_btn.setFixedWidth(60)
        xt_btn.clicked.connect(self._browse_xt)
        xt_row.addWidget(self._xt_input)
        xt_row.addWidget(xt_btn)
        form.addRow("XT XML", xt_row)

        # 已翻译插件（可选）
        tp_row = QHBoxLayout()
        self._tp_input = QLineEdit()
        self._tp_input.setPlaceholderText("可选，从已汉化插件提取译文")
        self._tp_input.setReadOnly(True)
        tp_btn = QPushButton("浏览")
        tp_btn.setFixedWidth(60)
        tp_btn.clicked.connect(self._browse_translated_plugin)
        tp_row.addWidget(self._tp_input)
        tp_row.addWidget(tp_btn)
        form.addRow("已翻译插件", tp_row)

        # 跳过空串
        self._skip_empty = QComboBox()
        self._skip_empty.addItems(["是", "否"])
        form.addRow("跳过空串", self._skip_empty)

        layout.addLayout(form)

        # 进度条（隐藏直到解析开始）
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # 不确定进度
        self._progress.hide()
        layout.addWidget(self._progress)

        self._status_lbl = QLabel("")
        layout.addWidget(self._status_lbl)

        # 解析按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._parse_btn = QPushButton("▶ 解析插件")
        self._parse_btn.setFixedHeight(32)
        self._parse_btn.clicked.connect(self._start_parse)
        btn_row.addWidget(self._parse_btn)
        layout.addLayout(btn_row)

    # ── File browser helpers ──────────────────────────────────

    def _browse_esp(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择插件文件", "", "ESP/ESM/ESL 文件 (*.esp *.esm *.esl);;所有文件 (*)"
        )
        if path:
            self._esp_input.setText(path)

    def _browse_eet(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 EET XML 文件", "", "XML 文件 (*.xml);;所有文件 (*)"
        )
        if path:
            self._eet_input.setText(path)

    def _browse_xt(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 XT XML 文件", "", "XML 文件 (*.xml);;所有文件 (*)"
        )
        if path:
            self._xt_input.setText(path)

    def _browse_translated_plugin(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择已翻译插件文件", "", "ESP/ESM/ESL 文件 (*.esp *.esm *.esl);;所有文件 (*)"
        )
        if path:
            self._tp_input.setText(path)

    # ── Parse ─────────────────────────────────────────────────

    def _start_parse(self):
        esp_path = self._esp_input.text().strip()
        if not esp_path:
            self._status_lbl.setText("请先选择插件文件")
            return

        skip_empty = self._skip_empty.currentText() == "是"
        eet_path = self._eet_input.text().strip() or None
        xt_path = self._xt_input.text().strip() or None
        tp_path = self._tp_input.text().strip() or None

        self._parse_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("解析中…")
        self.parse_started.emit()

        def _do_parse():
            parser = PluginParser()
            entries = parser.parse_plugin(Path(esp_path), skip_empty=skip_empty)
            collection = TranslationEntryCollection(entries)
            migrate_count = 0
            if eet_path:
                try:
                    migrate_count += collection.update_from_eet_xml(Path(eet_path))
                except Exception:
                    pass
            if xt_path:
                try:
                    xt_parser = XT_XmlParser.from_file(xt_path)
                    migrate_count += collection.apply_xt_entries(xt_parser.entries)
                except Exception:
                    pass
            if tp_path:
                try:
                    migrate_count += collection.update_from_translated_plugin(Path(tp_path))
                except Exception:
                    pass
            return collection, migrate_count, parser.get_plugin(), parser.get_strings_lookup()

        w = ApiWorker(_do_parse)
        w.result.connect(self._on_parse_done)
        w.error.connect(self._on_parse_error)
        w.start()
        self._workers.append(w)

    def _on_parse_done(self, result):
        collection, migrate_count, plugin, strings_lookup = result
        self._parse_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"解析完成，共 {len(collection)} 条词条")
        self._ctx.esp_path = self._esp_input.text().strip()
        self._ctx.eet_path = self._eet_input.text().strip() or None
        self._ctx.xt_path = self._xt_input.text().strip() or None
        self._ctx.migrate_count = migrate_count
        self._ctx.collection = collection
        self._ctx.plugin = plugin
        self._ctx.strings_lookup = strings_lookup
        self.parse_finished.emit(collection)

    def _on_parse_error(self, msg: str):
        self._parse_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"解析失败：{msg}")
        self.parse_finished.emit(None)
