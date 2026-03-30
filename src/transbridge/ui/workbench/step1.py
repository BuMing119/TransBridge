"""
步骤1：源文件选择与解析。
支持多集合管理：可同时打开多个插件/EET集合，通过顶部选择栏切换。
支持两种解析来源：ESP 插件（标准）或 EET XML（仅迁移旧译文）。
支持批量选择ESP文件，一次解析多个插件。
已加载集合可追加迁移源（每种仅限一次）。
"""

from pathlib import Path
from typing import List

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QPushButton, QFileDialog, QComboBox, QProgressBar, QLabel,
    QButtonGroup, QRadioButton, QMessageBox, QFrame, QCheckBox,
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

        self._import_json_btn = QPushButton("导入 JSON")
        self._import_json_btn.setFixedWidth(80)
        self._import_json_btn.clicked.connect(self._on_import_json)
        slot_row.addWidget(self._import_json_btn)

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

        # 应用到全部勾选框
        self._strings_apply_all = QCheckBox("全部")
        self._strings_apply_all.setToolTip("勾选后将Strings路径应用到所有已加载集合")
        strings_row.addWidget(self._strings_apply_all)

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

        # 解析按钮 & 应用迁移源按钮
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._apply_migrate_btn = QPushButton("应用迁移源")
        self._apply_migrate_btn.setFixedHeight(32)
        self._apply_migrate_btn.setVisible(False)
        self._apply_migrate_btn.clicked.connect(self._apply_migration_sources)
        btn_row.addWidget(self._apply_migrate_btn)
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
        self._parse_btn.setVisible(enabled)
        self._apply_migrate_btn.setVisible(not enabled)

    def _update_migration_buttons(self, slot: CollectionSlot | None):
        """根据当前slot状态更新迁移源按钮的可用性（每种迁移源只能配置一次）。"""
        if slot is None:
            return

        # ESP 路径永远锁定
        self._esp_browse_btn.setEnabled(False)

        # EET: 如果已有eet_path则禁用
        eet_enabled = slot.eet_path is None
        self._eet_browse_btn.setEnabled(eet_enabled)
        self._eet_clear_btn.setEnabled(False)  # 已加载集合不允许清除

        # XT: 如果已有xt_path则禁用
        xt_enabled = slot.xt_path is None
        self._xt_browse_btn.setEnabled(xt_enabled)
        self._xt_clear_btn.setEnabled(False)


        # Strings: 如果已有strings_path则禁用
        strings_enabled = slot.strings_path is None
        self._strings_browse_btn.setEnabled(strings_enabled)
        self._strings_clear_btn.setEnabled(False)
        self._strings_lang.setEnabled(strings_enabled)

        # 检查是否有可配置的迁移源
        has_available = eet_enabled or xt_enabled or strings_enabled
        self._apply_migrate_btn.setEnabled(has_available)

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
            is_eet_only = slot.esp_path is None and slot.eet_path is not None
            self._rb_esp.setChecked(not is_eet_only)
            self._rb_eet_only.setChecked(is_eet_only)
            self._esp_input.setText(slot.esp_path or "")
            self._eet_input.setText(slot.eet_path or "")
            self._xt_input.setText(slot.xt_path or "")
            self._tp_input.setText("")
            self._strings_input.setText(slot.strings_path or "")
            self._strings_lang.setCurrentText(slot.strings_lang or "chinese")
            self._set_locked(True)
            self._update_migration_buttons(slot)

    def _on_new_slot(self):
        """清空所有输入框，解锁表单，准备接受新一次解析。"""
        self._esp_input.clear()
        self._esp_input.setToolTip("")
        self._eet_input.clear()
        self._xt_input.clear()
        self._tp_input.clear()
        self._strings_input.clear()
        self._status_lbl.clear()
        self._rb_esp.setChecked(True)
        self._set_locked(False)
        if hasattr(self, '_esp_paths'):
            del self._esp_paths

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
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择插件文件（可多选）", "", "ESP/ESM/ESL 文件 (*.esp *.esm *.esl);;所有文件 (*)"
        )
        if paths:
            self._esp_paths: List[str] = paths
            if len(paths) == 1:
                self._esp_input.setText(paths[0])
            else:
                self._esp_input.setText(f"已选择 {len(paths)} 个文件")
            self._esp_input.setToolTip("\n".join(paths))

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

    # ── JSON Import ───────────────────────────────────────────

    def _on_import_json(self):
        """从 JSON 文件直接加载集合，无需解析插件。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 JSON 文件", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return

        self._status_lbl.setText("加载 JSON 中…")
        self._progress.show()

        def _do():
            return TranslationEntryCollection.from_json_file(path)

        def _on_done(collection):
            self._progress.hide()
            label = Path(path).stem
            slot = CollectionSlot(label=label, collection=collection)
            if path in self._ctx.slots:
                ret = QMessageBox.question(
                    self, "集合已存在",
                    f"集合「{label}」已存在，是否覆盖？\n选择「否」将保留原有集合。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    self._status_lbl.setText("已取消，保留原有集合")
                    self.parse_finished.emit(None)
                    return
            self._ctx.add_slot(path, slot)
            self._ctx.activate_slot(path)
            self._set_locked(True)
            self._update_migration_buttons(slot)
            self._status_lbl.setText(f"加载完成，共 {len(collection)} 条词条")
            self.parse_finished.emit(collection)

        def _on_error(msg: str):
            self._progress.hide()
            self._status_lbl.setText(f"加载失败：{msg}")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

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
            # 检查是否有批量选择的文件
            esp_paths = getattr(self, '_esp_paths', None)
            if esp_paths and len(esp_paths) > 1:
                # 批量解析模式
                eet_path = self._eet_input.text().strip() or None
                xt_path = self._xt_input.text().strip() or None
                tp_path = self._tp_input.text().strip() or None
                strings_dir = self._strings_input.text().strip() or None
                strings_lang = self._strings_lang.currentText()
                skip_empty = self._skip_empty.currentText() == "是"
                self._run_batch_parse_esp(esp_paths, eet_path, xt_path, tp_path, strings_dir, strings_lang, skip_empty)
            else:
                # 单文件模式
                esp_path = self._esp_input.text().strip()
                if not esp_path or esp_path.startswith("已选择"):
                    # 尝试从 _esp_paths 获取
                    if esp_paths and len(esp_paths) == 1:
                        esp_path = esp_paths[0]
                    else:
                        self._status_lbl.setText("请先选择插件文件")
                        return
                eet_path = self._eet_input.text().strip() or None
                xt_path = self._xt_input.text().strip() or None
                tp_path = self._tp_input.text().strip() or None
                strings_dir = self._strings_input.text().strip() or None
                strings_lang = self._strings_lang.currentText()
                skip_empty = self._skip_empty.currentText() == "是"
                self._run_parse_esp(esp_path, eet_path, xt_path, tp_path, strings_dir, strings_lang, skip_empty)

    def _run_batch_parse_esp(self, esp_paths: List[str], eet_path, xt_path, tp_path, strings_dir, strings_lang, skip_empty):
        """批量解析多个ESP文件。"""
        self._parse_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText(f"批量解析中 (0/{len(esp_paths)})…")
        self.parse_started.emit()

        self._batch_total = len(esp_paths)
        self._batch_current = 0
        self._batch_results = []
        self._batch_paths = esp_paths
        self._batch_eet_path = eet_path
        self._batch_xt_path = xt_path
        self._batch_tp_path = tp_path
        self._batch_strings_dir = strings_dir
        self._batch_strings_lang = strings_lang
        self._batch_skip_empty = skip_empty

        self._parse_next_in_batch()

    def _parse_next_in_batch(self):
        """解析批次中的下一个文件。"""
        if self._batch_current >= self._batch_total:
            # 批量解析完成
            self._finish_batch_parse()
            return

        esp_path = self._batch_paths[self._batch_current]
        self._status_lbl.setText(f"批量解析中 ({self._batch_current + 1}/{self._batch_total})…")

        def _do():
            parser = PluginParser()
            entries = parser.parse_plugin(Path(esp_path), skip_empty=self._batch_skip_empty)
            collection = TranslationEntryCollection(entries)
            migrate_count = 0
            # 批量模式不应用迁移源
            return collection, 0, parser.get_plugin(), parser.get_strings_lookup()

        def _on_done(result):
            collection, migrate_count, plugin, strings_lookup = result
            label = Path(esp_path).stem
            slot = CollectionSlot(
                label=label,
                collection=collection,
                esp_path=esp_path,
                eet_path=None,  # 批量模式不预设迁移源
                xt_path=None,
                strings_path=None,
                strings_lang=self._batch_strings_lang,
                migrate_count=migrate_count,
                plugin=plugin,
                strings_lookup=strings_lookup,
            )
            self._batch_results.append((esp_path, slot, collection))
            self._batch_current += 1
            self._parse_next_in_batch()

        def _on_error(msg: str):
            # 记录错误但继续下一个
            self._batch_results.append((self._batch_paths[self._batch_current], None, None))
            self._batch_current += 1
            self._parse_next_in_batch()

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _finish_batch_parse(self):
        """完成批量解析，注册所有槽位。"""
        self._parse_btn.setEnabled(True)
        self._progress.hide()

        success_count = sum(1 for _, slot, _ in self._batch_results if slot is not None)
        fail_count = self._batch_total - success_count

        # 注册所有成功的槽位
        for esp_path, slot, collection in self._batch_results:
            if slot:
                # 如果key已存在，覆盖
                self._ctx.add_slot(esp_path, slot)

        # 激活最后一个成功的槽位
        if self._batch_results:
            for esp_path, slot, _ in reversed(self._batch_results):
                if slot:
                    self._ctx.activate_slot(esp_path)
                    break

        self._set_locked(True)
        if self._ctx.active_slot:
            self._update_migration_buttons(self._ctx.active_slot)

        msg = f"批量解析完成：成功 {success_count} 个"
        if fail_count > 0:
            msg += f"，失败 {fail_count} 个"
        self._status_lbl.setText(msg)
        self.parse_finished.emit(self._ctx.collection)

        # 清理批量状态
        del self._batch_total
        del self._batch_current
        del self._batch_results
        del self._batch_paths
        del self._batch_eet_path
        del self._batch_xt_path
        del self._batch_tp_path
        del self._batch_strings_dir
        del self._batch_strings_lang
        del self._batch_skip_empty
        if hasattr(self, '_esp_paths'):
            del self._esp_paths

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
                strings_lang=strings_lang,
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

    # ── 应用迁移源 ─────────────────────────────────────────────

    def _apply_migration_sources(self):
        """将当前输入的迁移源应用到已加载的集合。"""
        slot = self._ctx.active_slot
        if not slot:
            return

        eet_path = self._eet_input.text().strip() or None
        xt_path = self._xt_input.text().strip() or None
        tp_path = self._tp_input.text().strip() or None
        strings_dir = self._strings_input.text().strip() or None
        strings_lang = self._strings_lang.currentText()
        apply_strings_to_all = self._strings_apply_all.isChecked() and strings_dir

        if not any([eet_path, xt_path, tp_path, strings_dir]):
            self._status_lbl.setText("请先选择迁移源文件")
            return

        self._apply_migrate_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("应用迁移源中…")

        def _do():
            migrate_count = 0
            updated_slots = []

            # 确定要处理的slot列表
            if apply_strings_to_all:
                slots_to_process = list(self._ctx.slots.values())
            else:
                slots_to_process = [slot]

            for s in slots_to_process:
                collection = s.collection
                slot_migrate = 0

                # 仅对当前slot应用EET/XT/TP
                if s is slot:
                    if eet_path and s.eet_path is None:
                        try:
                            slot_migrate += collection.update_from_eet_xml(Path(eet_path))
                        except Exception:
                            pass
                    if xt_path and s.xt_path is None:
                        try:
                            xp = XT_XmlParser.from_file(xt_path)
                            slot_migrate += collection.apply_xt_entries(xp.entries)
                        except Exception:
                            pass
                    if tp_path:
                        try:
                            slot_migrate += collection.update_from_translated_plugin(Path(tp_path))
                        except Exception:
                            pass

                # Strings可以应用到所有slot
                if strings_dir and s.strings_path is None:
                    try:
                        plugin_stem = Path(s.esp_path).stem if s.esp_path else ""
                        strings_lookup = PluginStringsLookup.from_strings_dir(
                            Path(strings_dir), plugin_stem, strings_lang
                        )
                        if strings_lookup:
                            slot_migrate += collection.update_from_strings_lookup(strings_lookup)
                            s.strings_lookup = strings_lookup
                    except Exception:
                        pass

                if slot_migrate > 0:
                    updated_slots.append((s, slot_migrate))
                migrate_count += slot_migrate

            return migrate_count, eet_path, xt_path, strings_dir, strings_lang, updated_slots

        def _on_done(result):
            migrate_count, new_eet, new_xt, new_strings, new_lang, updated_slots = result
            # 更新各 slot 中的路径
            for s, _ in updated_slots:
                if s is slot:
                    if new_eet and s.eet_path is None:
                        s.eet_path = new_eet
                    if new_xt and s.xt_path is None:
                        s.xt_path = new_xt
                if new_strings and s.strings_path is None:
                    s.strings_path = new_strings
                    s.strings_lang = new_lang

            self._progress.hide()
            if apply_strings_to_all and len(updated_slots) > 1:
                self._status_lbl.setText(f"迁移完成，共 {len(updated_slots)} 个集合，新增 {migrate_count} 条译文")
            else:
                self._status_lbl.setText(f"迁移完成，新增 {migrate_count} 条译文")
            self._ctx.collection_changed.emit(slot.collection)
            self._update_migration_buttons(slot)

        def _on_error(msg: str):
            self._progress.hide()
            self._status_lbl.setText(f"迁移失败：{msg}")
            self._apply_migrate_btn.setEnabled(True)

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)
