"""翻译词典管理面板：存为词典 + 词典库查看 + 分享导入 + 冲突仲裁。

用法：
    panel = DictionaryPanel(ctx, parent)
    panel.exec()
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.transbridge.translation_memory import TranslationMemoryManager


class DictionaryPanel(QDialog):
    """词典库管理面板。

    - 顶部：词典下拉（含「全部」）+ 词典标签筛选 + 「存为词典」「套用词典」「导入」「导出」「打开目录」「刷新」按钮
    - 主体：词典条目表格（原文/译文/来源/命中/词典标签/类型）
    """

    def __init__(self, ctx, parent=None, *, base_dir=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._manager = TranslationMemoryManager(base_dir=base_dir)
        self.setWindowTitle("翻译词典")
        self.resize(860, 560)

        self._build_ui()
        self._load()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # 顶栏
        top = QHBoxLayout()
        top.addWidget(QLabel("词典:"))
        self._dict_combo = QComboBox()
        self._dict_combo.currentIndexChanged.connect(self._on_dict_changed)
        top.addWidget(self._dict_combo, 1)

        top.addWidget(QLabel("词典标签:"))
        self._tag_combo = QComboBox()
        self._tag_combo.addItem("(全部)", "")
        self._tag_combo.currentIndexChanged.connect(self._refresh_table)
        top.addWidget(self._tag_combo, 1)

        self._save_btn = QPushButton("存为词典…")
        self._save_btn.clicked.connect(self._on_save_to_dict)
        top.addWidget(self._save_btn)

        self._apply_btn = QPushButton("套用词典")
        self._apply_btn.clicked.connect(self._on_apply_dict)
        top.addWidget(self._apply_btn)

        top.addLayout(self._build_share_buttons())
        layout.addLayout(top)

        # 表格
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["原文", "译文", "来源", "命中/词典标签/类型"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 220)
        self._table.setColumnWidth(1, 220)
        self._table.setColumnWidth(2, 120)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        # 状态栏文字
        self._status = QLabel("")
        layout.addWidget(self._status)

    def _build_share_buttons(self) -> QHBoxLayout:
        """导入/导出/打开目录 三按钮。"""
        row = QHBoxLayout()
        self._import_btn = QPushButton("导入")
        self._import_btn.clicked.connect(self._on_import_dict)
        row.addWidget(self._import_btn)

        self._export_btn = QPushButton("导出")
        self._export_btn.clicked.connect(self._on_export_dict)
        row.addWidget(self._export_btn)

        self._open_dir_btn = QPushButton("打开目录")
        self._open_dir_btn.clicked.connect(self._on_open_dir)
        row.addWidget(self._open_dir_btn)

        self._refresh_btn = QPushButton("刷新")
        self._refresh_btn.clicked.connect(self._load)
        row.addWidget(self._refresh_btn)
        return row

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            n = self._manager.load()
        except RuntimeError as exc:
            self._status.setText(f"加载失败: {exc}")
            return
        self._rebuild_combo()
        self._refresh_table()
        self._status.setText(f"已加载 {n} 本词典")

    def _rebuild_combo(self) -> None:
        current = self._dict_combo.currentData()
        self._dict_combo.blockSignals(True)
        self._dict_combo.clear()
        self._dict_combo.addItem("(全部词典)", None)
        for mod_id in sorted(self._manager.dictionaries.keys()):
            d = self._manager.dictionaries[mod_id]
            label = f"{mod_id} [{d.scope}]"
            self._dict_combo.addItem(label, mod_id)
        # 恢复选中
        if current is not None:
            idx = self._dict_combo.findData(current)
            if idx >= 0:
                self._dict_combo.setCurrentIndex(idx)
        self._dict_combo.blockSignals(False)
        # 重建词典标签下拉
        tags = {t for d in self._manager.dictionaries.values()
                for e in d.entries.values() for t in e.tags}
        self._tag_combo.blockSignals(True)
        self._tag_combo.clear()
        self._tag_combo.addItem("(全部)", "")
        for t in sorted(tags):
            self._tag_combo.addItem(t, t)
        self._tag_combo.blockSignals(False)

    def _on_dict_changed(self) -> None:
        self._refresh_table()

    def _current_dict_key(self):
        return self._dict_combo.currentData()

    def _refresh_table(self) -> None:
        key = self._current_dict_key()
        tag_filter = self._tag_combo.currentData()
        self._table.setRowCount(0)

        dicts = ([self._manager.dictionaries[key]]
                 if key is not None else list(self._manager.dictionaries.values()))

        rows = 0
        for d in dicts:
            # 键表条目
            for ck, idx in d.key_index.items():
                eid = idx.get("entry_id", "")
                entry = d.entries.get(eid)
                if entry is None:
                    continue
                if tag_filter and tag_filter not in entry.tags:
                    continue
                self._append_row(entry, idx.get("hits", 0), "键")
                rows += 1
            # 文本表条目（去重：同一 entry 若已在键表出现则跳过）
            seen = {idx.get("entry_id") for idx in d.key_index.values()}
            for nk, idx in d.text_index.items():
                eid = idx.get("entry_id", "")
                if eid in seen:
                    continue
                entry = d.entries.get(eid)
                if entry is None:
                    continue
                if tag_filter and tag_filter not in entry.tags:
                    continue
                self._append_row(entry, idx.get("hits", 0), "文本")
                rows += 1
        self._status.setText(f"显示 {rows} 条")

    def _append_row(self, entry, hits: int, via: str) -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(entry.original))
        self._table.setItem(r, 1, QTableWidgetItem(entry.translation))
        self._table.setItem(r, 2, QTableWidgetItem(entry.source_mod))
        meta = f"命中 {hits} | 词典标签 {','.join(entry.tags) if entry.tags else '-'} | {via}"
        self._table.setItem(r, 3, QTableWidgetItem(meta))

    # ------------------------------------------------------------------
    # 存为词典
    # ------------------------------------------------------------------

    def _on_save_to_dict(self) -> None:
        collection = self._ctx.collection
        if collection is None:
            QMessageBox.warning(self, "提示", "请先解析并加载翻译集合")
            return

        from src.transbridge.ui.tools.dictionary_dialog import SaveToDictionaryDialog
        dlg = SaveToDictionaryDialog(
            self,
            source_path=self._default_source_path(),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        mod_file_id, scope, selected_only, tags = dlg.result()

        # 确定目标条目
        if selected_only:
            entry_ids = list(self._ctx.selected_ids) if hasattr(self._ctx, "selected_ids") else []
            if not entry_ids:
                QMessageBox.information(self, "提示", "没有选中条目")
                return
        else:
            entry_ids = None

        try:
            added = self._manager.save_from_collection(
                collection, mod_file_id=mod_file_id, scope=scope,
                entry_ids=entry_ids, tags=tags,
            )
            self._manager.save()
            self._rebuild_combo()
            self._refresh_table()
            QMessageBox.information(self, "完成", f"已写入词典，新增 {added} 条词条")
        except Exception as exc:  # noqa: BLE001 - 用户可见错误
            QMessageBox.critical(self, "错误", f"存为词典失败: {exc}")

    def _default_source_path(self) -> str:
        """当前源文件路径（供 mod 名推断）。

        优先从 active_slot 取 esp_path（当前解析的插件文件）；
        其次取 eet_path / xt_path。
        """
        esp_path = getattr(self._ctx, "esp_path", None)
        if esp_path:
            return str(esp_path)
        eet_path = getattr(self._ctx, "eet_path", None)
        if eet_path:
            return str(eet_path)
        xt_path = getattr(self._ctx, "xt_path", None)
        if xt_path:
            return str(xt_path)
        return ""

    def _default_mod_id(self) -> str:
        """从源文件路径推断 mod 名（去扩展名）。"""
        sp = self._default_source_path()
        return Path(sp).stem if sp else ""

    def _on_apply_dict(self) -> None:
        collection = self._ctx.collection
        if collection is None:
            QMessageBox.warning(self, "提示", "请先解析并加载翻译集合")
            return

        from src.transbridge.translation_memory.manager import QueryContext

        manager = TranslationMemoryManager()
        try:
            manager.load()
        except RuntimeError as exc:
            QMessageBox.critical(self, "错误", f"词典加载失败: {exc}")
            return

        # 激活集默认：同名 mod 词典最优先（其余 project/global 自动纳入）
        context = QueryContext(mod_file_id=self._default_mod_id())
        result = manager.apply_to_collection(collection, context=context)

        msg = (
            f"套用完成：键命中 {result.key_hits} 条，文本命中 {result.text_hits} 条，"
            f"实际填充 {result.applied} 条，未命中 {result.misses} 条"
        )
        if result.needs_review:
            msg += f"\n\n⚠ {len(result.needs_review)} 条键命中但原文已变化（需复核）"

        # 冲突仲裁
        if result.conflicts:
            from src.transbridge.ui.tools.conflict_dialog import DictionaryConflictDialog
            dlg = DictionaryConflictDialog(result.conflicts, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                for entry_id, chosen in dlg.result():
                    entry = collection.get(entry_id)
                    if entry is not None:
                        entry.translation = chosen
                msg += f"\n\n已处理 {len(result.conflicts)} 处译文冲突"

        QMessageBox.information(self, "套用词典", msg)

    # ------------------------------------------------------------------
    # 分享 / 导入
    # ------------------------------------------------------------------

    def _on_import_dict(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入词典", "", "词典文件 (*.tbdict)"
        )
        if not path:
            return
        try:
            ok = self._manager.import_dict(Path(path))
        except (ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        if not ok:
            ret = QMessageBox.question(
                self, "同名词典", "存在同名词典，是否覆盖？"
            )
            if ret == QMessageBox.StandardButton.Yes:
                try:
                    self._manager.import_dict(Path(path), overwrite=True)
                except (ValueError, RuntimeError) as exc:
                    QMessageBox.critical(self, "导入失败", str(exc))
                    return
            else:
                return
        self._rebuild_combo()
        self._refresh_table()
        QMessageBox.information(self, "完成", "词典已导入")

    def _on_export_dict(self) -> None:
        mod_id = self._current_dict_key()
        if not mod_id:
            QMessageBox.warning(self, "提示", "请先在词典下拉中选择一个词典")
            return
        dest = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dest:
            return
        try:
            target = self._manager.export_dict(mod_id, dest)
            QMessageBox.information(self, "完成", f"已导出到 {target}")
        except Exception as exc:  # noqa: BLE001 - 用户可见错误
            QMessageBox.critical(self, "错误", f"导出失败: {exc}")

    def _on_open_dir(self) -> None:
        d = self._manager.default_dir()
        d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
