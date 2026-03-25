"""
步骤1：源文件选择与解析。
支持多集合管理：可同时打开多个插件/EET集合，通过顶部选择栏切换。
支持两种解析来源：ESP 插件（标准）或 EET XML（仅迁移旧译文）。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QPushButton, QFileDialog, QComboBox, QProgressBar, QLabel,
    QButtonGroup, QRadioButton, QMessageBox, QFrame,
)
from PyQt6.QtCore import pyqtSignal, Qt

from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.parser.plugin_parser import PluginParser
from src.transbridge.parser.eet_parser import EET_XmlParser
from src.transbridge.parser.xt_parser import XT_XmlParser
from src.transbridge.parser.strings_file import PluginStringsLookup
from src.transbridge.ui.context import CollectionSlot
from ..workers import ApiWorker


class Step1SourceWidget(QWidget):
    """源文件选择与解析面板。解析完成后将结果注册到 ctx 的集合槽位。"""

    parse_started = pyqtSignal()
    parse_finished = pyqtSignal(object)  # TranslationEntryCollection | None

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._workers: list[ApiWorker] = []
        self._init_ui()
        ctx.collection_list_changed.connect(self._refresh_combo)

    def _init_ui(self):
        box = QGroupBox("步骤1：源文件选择")
        outer = QVBoxLayout(self)
        outer.addWidget(box)
        outer.setContentsMargins(0, 0, 0, 0)

        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        # ── 集合选择栏 ─────────────────────────────────────────
        slot_row = QHBoxLayout()
        slot_row.addWidget(QLabel("当前集合："))
        self._slot_combo = QComboBox()
        self._slot_combo.setMinimumWidth(180)
        self._slot_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._slot_combo.currentIndexChanged.connect(self._on_slot_selected)
        slot_row.addWidget(self._slot_combo, stretch=1)

        self._new_btn = QPushButton("＋ 新建")
        self._new_btn.setFixedWidth(64)
        self._new_btn.clicked.connect(self._on_new_slot)
        slot_row.addWidget(self._new_btn)

        self._remove_btn = QPushButton("✕ 移除")
        self._remove_btn.setFixedWidth(64)
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._on_remove_slot)
        slot_row.addWidget(self._remove_btn)

        layout.addLayout(slot_row)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # ── 解析来源切换 ───────────────────────────────────────
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("解析来源："))
        self._src_group = QButtonGroup(self)
        self._rb_esp = QRadioButton("ESP 插件（标准）")
        self._rb_eet_only = QRadioButton("EET XML")
        self._rb_esp.setChecked(True)
        self._src_group.addButton(self._rb_esp, 0)
        self._src_group.addButton(self._rb_eet_only, 1)
        source_row.addWidget(self._rb_esp)
        source_row.addWidget(self._rb_eet_only)
        source_row.addStretch()
        layout.addLayout(source_row)
        self._rb_esp.toggled.connect(self._on_source_mode_changed)

        # ── 文件输入表单 ───────────────────────────────────────
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 插件文件（ESP 模式必填）
        self._esp_row_widget = QWidget()
        esp_row = QHBoxLayout(self._esp_row_widget)
        esp_row.setContentsMargins(0, 0, 0, 0)
        self._esp_input = QLineEdit()
        self._esp_input.setPlaceholderText("选择 .esp / .esm / .esl 文件…")
        self._esp_input.setReadOnly(True)
        self._esp_browse_btn = QPushButton("浏览")
        self._esp_browse_btn.setFixedWidth(60)
        self._esp_browse_btn.clicked.connect(self._browse_esp)
        esp_row.addWidget(self._esp_input)
        esp_row.addWidget(self._esp_browse_btn)
        self._esp_form_label = "插件文件 *"
        form.addRow(self._esp_form_label, self._esp_row_widget)

        # EET XML（ESP 模式可选迁移；EET 模式必填）
        self._eet_row_widget = QWidget()
        eet_row = QHBoxLayout(self._eet_row_widget)
        eet_row.setContentsMargins(0, 0, 0, 0)
        self._eet_input = QLineEdit()
        self._eet_input.setPlaceholderText("可选，迁移旧 EET 译文")
        self._eet_input.setReadOnly(True)
        self._eet_browse_btn = QPushButton("浏览")
        self._eet_browse_btn.setFixedWidth(60)
        self._eet_browse_btn.clicked.connect(self._browse_eet)
        eet_clear = QPushButton("✕")
        eet_clear.setFixedWidth(28)
        eet_clear.setToolTip("清除")
        eet_clear.clicked.connect(lambda: self._eet_input.clear())
        eet_row.addWidget(self._eet_input)
        eet_row.addWidget(self._eet_browse_btn)
        eet_row.addWidget(eet_clear)
        self._eet_clear_btn = eet_clear
        self._eet_form_label = "EET XML"
        form.addRow(self._eet_form_label, self._eet_row_widget)

        # XT XML（可选）
        self._xt_row_widget = QWidget()
        xt_row = QHBoxLayout(self._xt_row_widget)
        xt_row.setContentsMargins(0, 0, 0, 0)
        self._xt_input = QLineEdit()
        self._xt_input.setPlaceholderText("可选，迁移旧 XT 译文")
        self._xt_input.setReadOnly(True)
        self._xt_browse_btn = QPushButton("浏览")
        self._xt_browse_btn.setFixedWidth(60)
        self._xt_browse_btn.clicked.connect(self._browse_xt)
        self._xt_clear_btn = QPushButton("✕")
        self._xt_clear_btn.setFixedWidth(28)
        self._xt_clear_btn.setToolTip("清除")
        self._xt_clear_btn.clicked.connect(lambda: self._xt_input.clear())
        xt_row.addWidget(self._xt_input)
        xt_row.addWidget(self._xt_browse_btn)
        xt_row.addWidget(self._xt_clear_btn)
        form.addRow("XT XML", self._xt_row_widget)

        # 已翻译插件（可选）
        self._tp_row_widget = QWidget()
        tp_row = QHBoxLayout(self._tp_row_widget)
        tp_row.setContentsMargins(0, 0, 0, 0)
        self._tp_input = QLineEdit()
        self._tp_input.setPlaceholderText("可选，从已汉化插件提取译文")
        self._tp_input.setReadOnly(True)
        self._tp_browse_btn = QPushButton("浏览")
        self._tp_browse_btn.setFixedWidth(60)
        self._tp_browse_btn.clicked.connect(self._browse_translated_plugin)
        self._tp_clear_btn = QPushButton("✕")
        self._tp_clear_btn.setFixedWidth(28)
        self._tp_clear_btn.setToolTip("清除")
        self._tp_clear_btn.clicked.connect(lambda: self._tp_input.clear())
        tp_row.addWidget(self._tp_input)
        tp_row.addWidget(self._tp_browse_btn)
        tp_row.addWidget(self._tp_clear_btn)
        form.addRow("已翻译插件", self._tp_row_widget)

        # Strings 目录（可选，用于本地化插件导入翻译）
        self._strings_row_widget = QWidget()
        strings_row = QHBoxLayout(self._strings_row_widget)
        strings_row.setContentsMargins(0, 0, 0, 0)
        self._strings_input = QLineEdit()
        self._strings_input.setPlaceholderText("可选，从 Strings 文件导入翻译（本地化插件）")
        self._strings_input.setReadOnly(True)
        self._strings_browse_btn = QPushButton("浏览")
        self._strings_browse_btn.setFixedWidth(60)
        self._strings_browse_btn.clicked.connect(self._browse_strings_dir)
        self._strings_clear_btn = QPushButton("✕")
        self._strings_clear_btn.setFixedWidth(28)
        self._strings_clear_btn.setToolTip("清除")
        self._strings_clear_btn.clicked.connect(lambda: self._strings_input.clear())
        strings_row.addWidget(self._strings_input)
        strings_row.addWidget(self._strings_browse_btn)
        strings_row.addWidget(self._strings_clear_btn)

        # 语言选择
        self._strings_lang = QComboBox()
        self._strings_lang.addItems(["chinese", "english", "german", "french", "spanish", "italian", "japanese", "polish", "russian"])
        self._strings_lang.setFixedWidth(100)
        strings_row.addWidget(self._strings_lang)

        form.addRow("Strings 目录", self._strings_row_widget)

        # 跳过空串
        self._skip_empty = QComboBox()
        self._skip_empty.addItems(["是", "否"])
        form.addRow("跳过空串", self._skip_empty)

        self._form = form
        layout.addLayout(form)

        # 进度条（隐藏直到解析开始）
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
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

    # ── 锁定/解锁表单 ─────────────────────────────────────────

    def _set_locked(self, locked: bool):
        """已加载集合时锁定所有输入控件；新建模式时解锁。"""
        enabled = not locked
        self._rb_esp.setEnabled(enabled)
        self._rb_eet_only.setEnabled(enabled)
        self._esp_browse_btn.setEnabled(enabled)
        self._eet_browse_btn.setEnabled(enabled)
        self._eet_clear_btn.setEnabled(enabled)
        self._xt_browse_btn.setEnabled(enabled)
        self._xt_clear_btn.setEnabled(enabled)
        self._tp_browse_btn.setEnabled(enabled)
        self._tp_clear_btn.setEnabled(enabled)
        self._strings_browse_btn.setEnabled(enabled)
        self._strings_clear_btn.setEnabled(enabled)
        self._strings_lang.setEnabled(enabled)
        self._skip_empty.setEnabled(enabled)
        self._parse_btn.setEnabled(enabled)

    # ── 来源模式切换 ──────────────────────────────────────────

    def _on_source_mode_changed(self):
        esp_mode = self._rb_esp.isChecked()
        # EET 模式：仅隐藏 ESP 插件行；XT/已翻译插件仍可作为译文更新源
        self._esp_row_widget.setVisible(esp_mode)
        # EET 行：ESP 模式为可选（有清除按钮），EET 模式为必填（隐藏清除按钮）
        self._eet_clear_btn.setVisible(esp_mode)
        if esp_mode:
            self._eet_input.setPlaceholderText("可选，迁移旧 EET 译文")
            self._parse_btn.setText("▶ 解析插件")
        else:
            self._eet_input.setPlaceholderText("选择 EET XML 文件…")
            self._parse_btn.setText("▶ 解析 EET")

    # ── 集合选择栏 ────────────────────────────────────────────

    def _refresh_combo(self):
        """根据 ctx._slots 重建 ComboBox 内容。"""
        self._slot_combo.blockSignals(True)
        self._slot_combo.clear()
        for key, slot in self._ctx.slots.items():
            self._slot_combo.addItem(slot.label, userData=key)
        # 选中当前活跃 key
        active = self._ctx.active_key
        if active:
            idx = self._slot_combo.findData(active)
            if idx >= 0:
                self._slot_combo.setCurrentIndex(idx)
        self._slot_combo.blockSignals(False)
        self._remove_btn.setEnabled(bool(self._ctx.slots))

    def _on_slot_selected(self, index: int):
        if index < 0:
            return
        key = self._slot_combo.itemData(index)
        if key and key != self._ctx.active_key:
            self._ctx.activate_slot(key)
        # 同步输入框并锁定（无论是否切换，只要指向已存在的 slot 就锁定）
        slot = self._ctx.slots.get(key) if key else None
        if slot:
            self._rb_esp.setChecked(slot.esp_path is not None)
            self._rb_eet_only.setChecked(slot.esp_path is None)
            self._esp_input.setText(slot.esp_path or "")
            self._eet_input.setText(slot.eet_path or "")
            self._xt_input.setText(slot.xt_path or "")
            self._tp_input.setText("")
            self._set_locked(True)

    def _on_new_slot(self):
        """清空所有输入框，解锁表单，准备接受新一次解析。"""
        self._esp_input.clear()
        self._eet_input.clear()
        self._xt_input.clear()
        self._tp_input.clear()
        self._strings_input.clear()
        self._status_lbl.clear()
        self._rb_esp.setChecked(True)
        self._set_locked(False)

    def _on_remove_slot(self):
        active = self._ctx.active_key
        if not active:
            return
        slot = self._ctx.slots.get(active)
        label = slot.label if slot else active
        ret = QMessageBox.question(
            self, "移除集合",
            f"确定要移除集合「{label}」吗？\n已解析的数据将从内存中清除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._ctx.remove_slot(active)
            self._esp_input.clear()
            self._eet_input.clear()
            self._xt_input.clear()
            self._tp_input.clear()
            self._strings_input.clear()
            self._status_lbl.clear()
            # 若切换到了其他集合则锁定，否则解锁等待新建
            if self._ctx.active_key:
                self._set_locked(True)
            else:
                self._set_locked(False)

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

    def _browse_strings_dir(self):
        """选择 Strings 目录或 strings 文件。"""
        # 先尝试选择目录
        dir_path = QFileDialog.getExistingDirectory(
            self, "选择 Strings 目录", ""
        )
        if dir_path:
            self._strings_input.setText(dir_path)
            return
        # 如果用户取消，尝试选择文件
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Strings 文件", "",
            "Strings 文件 (*.strings *.dlstrings *.ilstrings);;所有文件 (*)"
        )
        if path:
            # 提取目录路径
            self._strings_input.setText(str(Path(path).parent))

    # ── Parse ─────────────────────────────────────────────────

    def _start_parse(self):
        eet_only = self._rb_eet_only.isChecked()

        if eet_only:
            eet_path = self._eet_input.text().strip()
            if not eet_path:
                self._status_lbl.setText("请先选择 EET XML 文件")
                return
            self._run_parse_eet(eet_path)
        else:
            esp_path = self._esp_input.text().strip()
            if not esp_path:
                self._status_lbl.setText("请先选择插件文件")
                return
            eet_path = self._eet_input.text().strip() or None
            xt_path = self._xt_input.text().strip() or None
            tp_path = self._tp_input.text().strip() or None
            strings_dir = self._strings_input.text().strip() or None
            strings_lang = self._strings_lang.currentText()
            skip_empty = self._skip_empty.currentText() == "是"
            self._run_parse_esp(esp_path, eet_path, xt_path, tp_path, strings_dir, strings_lang, skip_empty)

    def _run_parse_esp(self, esp_path, eet_path, xt_path, tp_path, strings_dir, strings_lang, skip_empty):
        self._parse_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("解析中…")
        self.parse_started.emit()

        def _do():
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
                    xp = XT_XmlParser.from_file(xt_path)
                    migrate_count += collection.apply_xt_entries(xp.entries)
                except Exception:
                    pass
            if tp_path:
                try:
                    migrate_count += collection.update_from_translated_plugin(Path(tp_path))
                except Exception:
                    pass
            if strings_dir:
                try:
                    plugin_stem = Path(esp_path).stem
                    strings_lookup = PluginStringsLookup.from_strings_dir(
                        Path(strings_dir), plugin_stem, strings_lang
                    )
                    if strings_lookup:
                        migrate_count += collection.update_from_strings_lookup(strings_lookup)
                except Exception:
                    pass
            return collection, migrate_count, parser.get_plugin(), parser.get_strings_lookup()

        def _on_done(result):
            collection, migrate_count, plugin, strings_lookup = result
            label = Path(esp_path).stem
            slot = CollectionSlot(
                label=label,
                collection=collection,
                esp_path=esp_path,
                eet_path=eet_path,
                xt_path=xt_path,
                strings_path=strings_dir,
                migrate_count=migrate_count,
                plugin=plugin,
                strings_lookup=strings_lookup,
            )
            self._finish_parse(esp_path, slot, collection)

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(self._on_parse_error)
        w.start()
        self._workers.append(w)

    def _run_parse_eet(self, eet_path):
        xt_path = self._xt_input.text().strip() or None
        tp_path = self._tp_input.text().strip() or None

        self._parse_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("解析 EET 中…")
        self.parse_started.emit()

        def _do():
            collection = TranslationEntryCollection.from_eet_xml(Path(eet_path))
            migrate_count = 0
            if xt_path:
                try:
                    xp = XT_XmlParser.from_file(xt_path)
                    migrate_count += collection.apply_xt_entries(xp.entries)
                except Exception:
                    pass
            if tp_path:
                try:
                    migrate_count += collection.update_from_translated_plugin(Path(tp_path))
                except Exception:
                    pass
            return collection, migrate_count

        def _on_done(result):
            collection, migrate_count = result
            label = Path(eet_path).stem
            slot = CollectionSlot(
                label=label,
                collection=collection,
                eet_path=eet_path,
                xt_path=xt_path,
                migrate_count=migrate_count,
            )
            self._finish_parse(eet_path, slot, collection)

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(self._on_parse_error)
        w.start()
        self._workers.append(w)

    def _finish_parse(self, key: str, slot: CollectionSlot, collection):
        self._parse_btn.setEnabled(True)
        self._progress.hide()

        # 若 key 已存在，询问是否覆盖
        if key in self._ctx.slots:
            ret = QMessageBox.question(
                self, "集合已存在",
                f"集合「{slot.label}」已存在，是否覆盖？\n选择「否」将保留原有集合。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                self._status_lbl.setText("已取消，保留原有集合")
                self.parse_finished.emit(None)
                return

        self._ctx.add_slot(key, slot)
        self._set_locked(True)
        self._status_lbl.setText(f"解析完成，共 {len(collection)} 条词条")
        self.parse_finished.emit(collection)

    def _on_parse_error(self, msg: str):
        self._parse_btn.setEnabled(True)
        self._progress.hide()
        self._status_lbl.setText(f"解析失败：{msg}")
        self.parse_finished.emit(None)
