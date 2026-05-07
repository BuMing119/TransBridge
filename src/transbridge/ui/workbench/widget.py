"""
WorkbenchWidget: 工作台布局（集合工具栏 + 词条预览全宽）。
Story-18: Step1/Step3 面板已移除。
Story-19: 左侧 CollectionStatsPanel 已移除，分类统计以标签组形式嵌入 Step2。
"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QFileDialog, QMessageBox,
)

from .step2 import Step2PreviewWidget


class WorkbenchWidget(QWidget):

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._tool_windows: dict = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── 集合管理工具栏 ─────────────────────────────────
        self._collection_bar = self._build_collection_bar()
        layout.addWidget(self._collection_bar)

        self._step2 = Step2PreviewWidget(self._ctx)
        layout.addWidget(self._step2)

        self._ctx.collection_list_changed.connect(self._rebuild_collection_combo)
        self._rebuild_collection_combo()

    # ── Step2 progress proxy ──────────────────────────────────

    def show_step2_progress(self, total: int, msg: str = ""):
        self._step2.show_progress(total, msg)

    def update_step2_progress(self, current: int, total: int, msg: str):
        self._step2.update_progress(current, total, msg)

    def hide_step2_progress(self):
        self._step2.hide_progress()

    def set_step2_parsing(self, parsing: bool):
        self._step2.set_parsing(parsing)

    # ── Tool windows ──────────────────────────────────────────

    def open_tool(self, tool_id: str):
        if tool_id == "ai_translator":
            progress_win = self._tool_windows.get("ai_translator_progress")
            if progress_win is not None and progress_win.is_running():
                progress_win.show()
                progress_win.raise_()
                progress_win.activateWindow()
                return

            from src.transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
            win = AITranslatorWindow.open_for_translation(self._ctx, self._step2, parent=self)
            if win is None:
                return

            if isinstance(win, AITranslatorWindow):
                win.progress_window_created.connect(self._on_progress_window_created)
                self._tool_windows["ai_translator"] = win
            else:
                self._tool_windows["ai_translator_progress"] = win
            return

        if tool_id in self._tool_windows:
            win = self._tool_windows[tool_id]
            win.show()
            win.raise_()
            win.activateWindow()

    def _on_progress_window_created(self, progress_win):
        self._tool_windows["ai_translator_progress"] = progress_win
        self._tool_windows.pop("ai_translator", None)

    # ── Collection toolbar ───────────────────────────────────

    def _build_collection_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        row.addWidget(QLabel("集合:"))

        self._collection_combo = QComboBox()
        self._collection_combo.setMinimumWidth(180)
        self._collection_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self._collection_combo.currentIndexChanged.connect(self._on_collection_switch)
        row.addWidget(self._collection_combo, stretch=1)

        self._btn_new = QPushButton("+ 新建")
        self._btn_new.setFlat(True)
        self._btn_new.setToolTip("清空当前选择，准备加载新数据")
        self._btn_new.clicked.connect(self._on_new_slot)
        row.addWidget(self._btn_new)

        self._btn_import = QPushButton("导入JSON")
        self._btn_import.setFlat(True)
        self._btn_import.setToolTip("从 JSON 文件导入翻译集合")
        self._btn_import.clicked.connect(self._on_import_json)
        row.addWidget(self._btn_import)

        self._btn_remove = QPushButton("✕ 移除")
        self._btn_remove.setFlat(True)
        self._btn_remove.setToolTip("移除当前集合（不删除文件）")
        self._btn_remove.clicked.connect(self._on_remove_slot)
        row.addWidget(self._btn_remove)

        return bar

    def _rebuild_collection_combo(self):
        """根据 ctx.slots 重建下拉列表。无集合时禁用控件。"""
        slots = self._ctx.slots
        active = self._ctx.active_key

        self._collection_combo.blockSignals(True)
        try:
            self._collection_combo.clear()
            if not slots:
                self._collection_combo.addItem("（无集合）")
                self._collection_combo.setEnabled(False)
                self._btn_remove.setEnabled(False)
                return

            self._collection_combo.setEnabled(True)
            self._btn_remove.setEnabled(True)
            active_idx = 0
            for i, (key, slot) in enumerate(slots.items()):
                label = slot.label or (Path(key).stem if key else "?")
                self._collection_combo.addItem(label, key)
                if key == active:
                    active_idx = i
            self._collection_combo.setCurrentIndex(active_idx)
        finally:
            self._collection_combo.blockSignals(False)

    def _on_collection_switch(self, index: int):
        key = self._collection_combo.itemData(index)
        if key and key != self._ctx.active_key:
            self._ctx.activate_slot(key)

    def _on_new_slot(self):
        self._ctx.activate_slot("")

    def _on_import_json(self):
        from src.transbridge.converter.translation_entry_collection import (
            TranslationEntryCollection,
        )
        from src.transbridge.ui.context import CollectionSlot
        from src.transbridge.ui.workers import ApiWorker

        path, _ = QFileDialog.getOpenFileName(
            self, "导入 JSON 文件", "", "JSON 文件 (*.json);;所有文件 (*)"
        )
        if not path:
            return

        self.show_step2_progress(0, "加载 JSON 中…")

        def _do():
            return TranslationEntryCollection.from_json_file(path)

        def _on_done(collection):
            self.hide_step2_progress()
            label = Path(path).stem
            slot = CollectionSlot(label=label, collection=collection)
            if path in self._ctx.slots:
                ret = QMessageBox.question(
                    self, "集合已存在",
                    f"集合「{label}」已存在，是否覆盖？\n选择「否」将保留原有集合。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if ret != QMessageBox.StandardButton.Yes:
                    return
            self._ctx.add_slot(path, slot)
            self._ctx.activate_slot(path)

        def _on_error(msg: str):
            self.hide_step2_progress()

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()

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
