# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""翻译词典管理面板：存为词典 + 词典库查看 + 分享导入 + 冲突仲裁。

用法：
    panel = DictionaryPanel(ctx, parent)
    panel.exec()
"""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from transbridge.application.projects.dictionary_apply import (
    dictionary_mod_id,
    dictionary_source_path,
    prepare_dictionary,
)
from transbridge.application.tasks.controls import CancellationToken
from transbridge.persistence.v2.ids import ProjectId, VariantId, VariantRef
from transbridge.translation_memory import TranslationMemoryManager
from transbridge.translation_memory.publication import capture_dictionary_save, publish_dictionary
from transbridge.ui.foundation.components import WrappingLabel
from transbridge.ui.workers import ApiWorker


class DictionaryPanel(QDialog):
    """词典库管理面板。

    - 顶部：词典下拉（含「全部」）+ 词典标签筛选 + 「存为词典」「套用词典」「导入」「导出」「打开目录」「刷新」按钮
    - 主体：词典条目表格（原文/译文/来源/命中/词典标签/类型）
    """

    def __init__(self, ctx, parent=None, *, base_dir=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._manager = TranslationMemoryManager(base_dir=base_dir)
        self._save_cancel: Event | None = None
        self._save_progress: QProgressDialog | None = None
        self.finished.connect(self._cancel_dictionary_save)
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
        self._status = WrappingLabel()
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
        tags = {t for d in self._manager.dictionaries.values() for e in d.entries.values() for t in e.tags}
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

        dicts = [self._manager.dictionaries[key]] if key is not None else list(self._manager.dictionaries.values())

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

        from transbridge.ui.tools.dictionary_dialog import SaveToDictionaryDialog

        dlg = SaveToDictionaryDialog(
            self,
            source_path=dictionary_source_path(self._ctx),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if self._ctx.collection is not collection:
            QMessageBox.warning(self, "提示", "活动来源已变化，请重新存为词典。")
            return
        mod_file_id, scope, selected_only, tags = dlg.result()
        try:
            selected = (
                (entry.identity for entry in getattr(self._ctx, "selected_entries", ())) if selected_only else None
            )
            save = capture_dictionary_save(collection, mod_file_id, scope, selected, tags)
        except Exception as exc:  # noqa: BLE001 - 用户可见错误
            QMessageBox.critical(self, "错误", f"存为词典失败: {exc}")
            return
        stopped = Event()
        token = CancellationToken(stopped)
        manager = self._manager
        progress = QProgressDialog("正在存为词典…", "取消", 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        self._save_progress = progress
        self._save_cancel = stopped
        progress.canceled.connect(stopped.set)
        worker = ApiWorker(
            lambda: publish_dictionary(
                manager, save, commit=lambda publish: publish(), checkpoint=token.raise_if_cancelled
            ),
            route_http_errors=False,
            cancel_read_on_close=stopped.set,
        )

        worker.result.connect(self._dictionary_saved)
        worker.error.connect(self._dictionary_save_failed)
        worker.finished.connect(self._dictionary_save_finished)
        worker.finished.connect(worker.deleteLater)
        self._save_btn.setEnabled(False)
        progress.show()
        try:
            worker.start()
        except RuntimeError as exc:
            self._dictionary_save_failed(str(exc))
            self._dictionary_save_finished()
            worker.deleteLater()

    def _cancel_dictionary_save(self) -> None:
        if self._save_cancel is not None:
            self._save_cancel.set()

    def _dictionary_saved(self, result):
        self._save_progress.hide()
        added, dictionary = result
        if dictionary is not None:
            self._manager.dictionaries[dictionary.mod_file_id] = dictionary
        self._rebuild_combo()
        self._refresh_table()
        if not self._save_cancel.is_set():
            QMessageBox.information(self, "完成", f"已写入词典，新增 {added} 条词条")

    def _dictionary_save_failed(self, message):
        self._save_progress.hide()
        if not self._save_cancel.is_set():
            QMessageBox.critical(self, "错误", f"存为词典失败: {message}")

    def _dictionary_save_finished(self):
        self._save_progress.deleteLater()
        self._save_progress = None
        self._save_cancel = None
        self._save_btn.setEnabled(True)

    def _on_apply_dict(self) -> None:
        collection = self._ctx.collection
        if collection is None:
            QMessageBox.warning(self, "提示", "请先解析并加载翻译集合")
            return

        expected_identity = getattr(self._ctx, "active_version_identity", None)
        expected_project_revision = getattr(self._ctx, "project_revision", None)
        expected_variant_revision = getattr(self._ctx, "variant_revision", None)
        commands = getattr(self._ctx, "project_commands", None)
        runtime_context = getattr(self._ctx, "runtime_context", None)
        if commands is None or runtime_context is None or expected_identity is None:
            error = QMessageBox(
                QMessageBox.Icon.Critical,
                "套用失败",
                "工程写入服务不可用，词典尚未套用。",
                parent=self,
            )
            error.setDetailedText("权威 Variant 写入适配器不可用。")
            error.exec()
            return
        try:
            application = prepare_dictionary(collection, self._manager, mod_file_id=dictionary_mod_id(self._ctx))
        except RuntimeError as exc:
            QMessageBox.critical(self, "套用失败", str(exc))
            return

        result = application.result
        msg = (
            f"套用完成：键命中 {result.key_hits} 条，文本命中 {result.text_hits} 条，"
            f"实际填充 {result.applied} 条，未命中 {result.misses} 条"
        )
        if result.needs_review:
            msg += f"\n\n⚠ {len(result.needs_review)} 条键命中但原文已变化（需复核）"

        if application.choices:
            selections = self._ctx.review_dictionary_conflicts(application.choices, self)
            application.apply_choices(selections)
            if selections:
                msg += f"\n\n已处理 {len(selections)} 处译文冲突"

        project_id, variant_id = expected_identity
        commit = application.commit(
            commands,
            runtime_context,
            expected_project_revision=expected_project_revision,
            expected_variant_revision=expected_variant_revision,
            expected_variant_ref=VariantRef(VariantId(variant_id), ProjectId(project_id)),
        )
        if not commit.is_success:
            detail = commit.diagnostics[0].message if commit.diagnostics else "Variant 提交失败"
            QMessageBox.critical(self, "套用失败", f"{detail}\n\n工程译文未改变。")
            return
        self._ctx.collection_changed.emit(self._ctx.collection)

        QMessageBox.information(self, "套用词典", msg)

    # ------------------------------------------------------------------
    # 分享 / 导入
    # ------------------------------------------------------------------

    def _on_import_dict(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入词典", "", "词典文件 (*.tbdict)")
        if not path:
            return
        try:
            ok = self._manager.import_dict(Path(path))
        except (ValueError, RuntimeError) as exc:
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        if not ok:
            ret = QMessageBox.question(
                self,
                "同名词典",
                "存在同名词典，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
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
