"""
步骤1：源文件选择与解析。
支持多集合管理：可同时打开多个插件/EET集合，通过顶部选择栏切换。
支持两种解析来源：ESP 插件（标准）或 EET XML（仅迁移旧译文）。
支持批量选择ESP文件，一次解析多个插件。
已加载集合可追加迁移源（每种仅限一次）。
"""

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser
from transbridge.parser.strings_file import PluginStringsLookup
from transbridge.parser.xt import XT_XmlParser
from transbridge.ui.context import CollectionSlot
from transbridge.ui.workbench.parse_presenter import MigrationRequest, ParsePresenter
from transbridge.ui.workbench.source_input_view import (
    SourceInputCallbacks,
    SourceInputView,
)

from ..workers import ApiWorker


class Step1SourceWidget(QWidget):
    """源文件选择与解析面板。解析完成后将结果注册到 ctx 的集合槽位。"""

    parse_started = pyqtSignal()
    parse_finished = pyqtSignal(object)  # TranslationEntryCollection | None

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._parse_presenter = ParsePresenter()
        self._workers = self._parse_presenter.workers
        self._init_composed_ui()
        ctx.collection_list_changed.connect(self._refresh_combo)

    def _init_composed_ui(self) -> None:
        """Compose the source view while preserving the facade's widget aliases."""
        self._source_view = SourceInputView(
            SourceInputCallbacks(
                select_slot=self._on_slot_selected,
                new_slot=self._on_new_slot,
                import_json=self._on_import_json,
                remove_slot=self._on_remove_slot,
                source_mode_changed=self._on_source_mode_changed,
                apply_migration=self._apply_migration_sources,
                start_parse=self._start_parse,
            ),
            self,
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._source_view)
        aliases = {
            "_slot_combo": "slot_combo",
            "_new_btn": "new_button",
            "_import_json_btn": "import_json_button",
            "_remove_btn": "remove_button",
            "_src_group": "source_group",
            "_rb_esp": "esp_radio",
            "_rb_eet_only": "eet_only_radio",
            "_esp_row_widget": "esp_row_widget",
            "_esp_input": "esp_input",
            "_esp_browse_btn": "esp_browse_button",
            "_eet_row_widget": "eet_row_widget",
            "_eet_input": "eet_input",
            "_eet_browse_btn": "eet_browse_button",
            "_eet_clear_btn": "eet_clear_button",
            "_xt_row_widget": "xt_row_widget",
            "_xt_input": "xt_input",
            "_xt_browse_btn": "xt_browse_button",
            "_xt_clear_btn": "xt_clear_button",
            "_tp_row_widget": "translated_plugin_row_widget",
            "_tp_input": "translated_plugin_input",
            "_tp_browse_btn": "translated_plugin_browse_button",
            "_tp_clear_btn": "translated_plugin_clear_button",
            "_sst_row_widget": "sst_row_widget",
            "_sst_input": "sst_input",
            "_sst_browse_btn": "sst_browse_button",
            "_sst_clear_btn": "sst_clear_button",
            "_strings_row_widget": "strings_row_widget",
            "_strings_input": "strings_input",
            "_strings_browse_btn": "strings_browse_button",
            "_strings_clear_btn": "strings_clear_button",
            "_strings_lang": "strings_language",
            "_strings_apply_all": "strings_apply_all",
            "_skip_empty": "skip_empty",
            "_form": "form",
            "_progress": "progress",
            "_status_lbl": "status_label",
            "_apply_migrate_btn": "apply_migration_button",
            "_parse_btn": "parse_button",
        }
        for legacy_name, view_name in aliases.items():
            setattr(self, legacy_name, getattr(self._source_view, view_name))
        self._esp_form_label = "插件文件 *"
        self._eet_form_label = "EET XML"

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
        self._sst_browse_btn.setEnabled(enabled)
        self._sst_clear_btn.setEnabled(enabled)
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

        # SST: 如果已有sst_path则禁用
        sst_enabled = getattr(slot, "sst_path", None) is None
        self._sst_browse_btn.setEnabled(sst_enabled)
        self._sst_clear_btn.setEnabled(False)

        # Strings: 如果已有strings_path则禁用
        strings_enabled = slot.strings_path is None
        self._strings_browse_btn.setEnabled(strings_enabled)
        self._strings_clear_btn.setEnabled(False)
        self._strings_lang.setEnabled(strings_enabled)

        # 检查是否有可配置的迁移源
        has_available = eet_enabled or xt_enabled or sst_enabled or strings_enabled
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
            self._sst_input.setText(getattr(slot, "sst_path", "") or "")
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
        self._sst_input.clear()
        self._strings_input.clear()
        self._status_lbl.clear()
        self._rb_esp.setChecked(True)
        self._set_locked(False)
        if hasattr(self, "_esp_paths"):
            del self._esp_paths
        if hasattr(self._source_view, "esp_paths"):
            del self._source_view.esp_paths

    def _on_remove_slot(self):
        active = self._ctx.active_key
        if not active:
            return
        slot = self._ctx.slots.get(active)
        label = slot.label if slot else active
        ret = QMessageBox.question(
            self,
            "移除集合",
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

    # ── JSON Import ───────────────────────────────────────────

    def _on_import_json(self):
        """从 JSON 文件直接加载集合，无需解析插件。"""
        path, _ = QFileDialog.getOpenFileName(self, "导入 JSON 文件", "", "JSON 文件 (*.json);;所有文件 (*)")
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
                    self,
                    "集合已存在",
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
        self._parse_presenter.track(w)

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
            esp_paths = getattr(
                self._source_view,
                "esp_paths",
                getattr(self, "_esp_paths", None),
            )
            if esp_paths and len(esp_paths) > 1:
                # 批量解析模式
                eet_path = self._eet_input.text().strip() or None
                xt_path = self._xt_input.text().strip() or None
                sst_path = self._sst_input.text().strip() or None
                tp_path = self._tp_input.text().strip() or None
                strings_dir = self._strings_input.text().strip() or None
                strings_lang = self._strings_lang.currentText()
                skip_empty = self._skip_empty.currentText() == "是"
                self._run_batch_parse_esp(
                    esp_paths, eet_path, xt_path, sst_path, tp_path, strings_dir, strings_lang, skip_empty
                )
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
                sst_path = self._sst_input.text().strip() or None
                tp_path = self._tp_input.text().strip() or None
                strings_dir = self._strings_input.text().strip() or None
                strings_lang = self._strings_lang.currentText()
                skip_empty = self._skip_empty.currentText() == "是"
                self._run_parse_esp(
                    esp_path, eet_path, xt_path, sst_path, tp_path, strings_dir, strings_lang, skip_empty
                )

    def _run_batch_parse_esp(
        self, esp_paths: list[str], eet_path, xt_path, sst_path, tp_path, strings_dir, strings_lang, skip_empty
    ):
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
        self._batch_sst_path = sst_path
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
        self._parse_presenter.track(w)

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
        del self._batch_sst_path
        del self._batch_tp_path
        del self._batch_strings_dir
        del self._batch_strings_lang
        del self._batch_skip_empty
        if hasattr(self, "_esp_paths"):
            del self._esp_paths
        if hasattr(self._source_view, "esp_paths"):
            del self._source_view.esp_paths

    def _run_parse_esp(self, esp_path, eet_path, xt_path, sst_path, tp_path, strings_dir, strings_lang, skip_empty):
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
            if sst_path:
                try:
                    from transbridge.parser.xt.sst_parser import SST_Parser

                    sp = SST_Parser.from_file(sst_path)
                    result = collection.apply_sst_entries(sp.entries)
                    migrate_count += result["updated"]
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
                    strings_lookup = PluginStringsLookup.from_strings_dir(Path(strings_dir), plugin_stem, strings_lang)
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
                sst_path=sst_path,
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
        self._parse_presenter.track(w)

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
        self._parse_presenter.track(w)

    def _finish_parse(self, key: str, slot: CollectionSlot, collection):
        self._parse_btn.setEnabled(True)
        self._progress.hide()

        # 若 key 已存在，询问是否覆盖
        if key in self._ctx.slots:
            ret = QMessageBox.question(
                self,
                "集合已存在",
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
        sst_path = self._sst_input.text().strip() or None
        strings_dir = self._strings_input.text().strip() or None
        strings_lang = self._strings_lang.currentText()
        apply_strings_to_all = self._strings_apply_all.isChecked() and strings_dir

        if not any([eet_path, xt_path, tp_path, sst_path, strings_dir]):
            self._status_lbl.setText("请先选择迁移源文件")
            return

        self._apply_migrate_btn.setEnabled(False)
        self._progress.show()
        self._status_lbl.setText("应用迁移源中…")

        request = MigrationRequest(
            eet_path=eet_path,
            xt_path=xt_path,
            translated_plugin_path=tp_path,
            sst_path=sst_path,
            strings_dir=strings_dir,
            strings_language=strings_lang,
            apply_strings_to_all=bool(apply_strings_to_all),
        )

        def _do():
            return self._parse_presenter.apply_migration(self._ctx, slot, request)

        def _on_done(result):
            migrate_count = result.migrated_count
            updated_slots = result.updated_slots
            request = result.request
            # 更新各 slot 中的路径
            for s, _ in updated_slots:
                if s is slot:
                    if request.eet_path and s.eet_path is None:
                        s.eet_path = request.eet_path
                    if request.xt_path and s.xt_path is None:
                        s.xt_path = request.xt_path
                    if request.sst_path and getattr(s, "sst_path", None) is None:
                        s.sst_path = request.sst_path
                if request.strings_dir and s.strings_path is None:
                    s.strings_path = request.strings_dir
                    s.strings_lang = request.strings_language

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
        self._parse_presenter.track(w)
