from pathlib import Path
from typing import List

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel, QMessageBox,
    QFileDialog, QMenu, QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, QSettings, QObject
from PyQt6.QtGui import QShortcut, QKeySequence

from pathlib import Path as PathLib

from transbridge import __version__
from .context import AppContext, CollectionSlot
from .workers import ApiWorker, get_http_error_bus, get_api_status_bus
from .workbench.widget import WorkbenchWidget
from .paratranz.widget import ParaTranzWidget
from .paratranz.config_dialog import ConfigDialog
from src.transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection
from src.transbridge.persistence import WorkspaceState, ProjectHandle, VariantStore, PERSISTENCE_ROOT, workspace_path


class _ApiStatusIndicator(QLabel):
    """状态栏 API 状态指示器：绿点（正常）/ 转圈动画（请求中）/ 红点（异常）。"""

    _SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = 0
        self._last_ok = True
        self._spin_idx = 0

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

        self._refresh()

    def on_request_started(self):
        if self._active == 0:
            self._last_ok = True
        self._active += 1
        if not self._timer.isActive():
            self._timer.start()
        self._refresh()

    def on_request_finished(self, success: bool):
        self._active = max(0, self._active - 1)
        if not success:
            self._last_ok = False
        if self._active == 0:
            self._timer.stop()
        self._refresh()

    def _tick(self):
        self._spin_idx = (self._spin_idx + 1) % len(self._SPINNER)
        self._refresh()

    def _refresh(self):
        if self._active > 0:
            self.setText(
                f'<span style="color:#888">{self._SPINNER[self._spin_idx]} 请求中</span>'
            )
        elif self._last_ok:
            self.setText('<span style="color:green">● 正常</span>')
        else:
            self.setText('<span style="color:red">● 异常</span>')


class _AutoSaveManager(QObject):
    """管理自动保存：QTimer 定时器 + 防抖。"""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._mw = main_window
        self._interval_timer = QTimer(self)
        self._interval_timer.timeout.connect(self._auto_save)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._auto_save)

    def start(self, interval_minutes: int = 5):
        self._interval_timer.start(interval_minutes * 60000)

    def stop(self):
        self._interval_timer.stop()
        self._debounce_timer.stop()

    def trigger_debounce(self):
        """操作触发防抖——重启 2s 定时器。"""
        self._debounce_timer.start(2000)

    def _auto_save(self):
        ctx = self._mw._ctx
        vs = ctx.variant_store
        if vs is None or not vs.dirty:
            return
        try:
            if ctx.collection:
                wb = getattr(self._mw, '_workbench', None)
                step2 = getattr(wb, '_step2', None) if wb else None
                labels, label_lib = ({}, {})
                if step2:
                    labels, label_lib = step2.collect_labels()
                vs.collect_from(list(ctx.collection), labels, label_lib)
            vs.save()
            vs.dirty = False
            # 更新保存按钮状态
            try:
                self._mw._workbench._project_bar.set_save_dirty(False)
            except Exception:
                pass
            self._mw.show_message("已自动保存")
        except Exception as e:
            import sys, traceback
            print(f"[自动保存失败] {e}", file=sys.stderr)
            traceback.print_exc()


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TransBridge")
        self.resize(1280, 820)

        self._ctx = AppContext(self)
        self._workers: list[ApiWorker] = []
        self._assistant_panel = None

        self._setup_op_cards()
        self._init_menu()
        self._init_shortcuts()
        self._init_central()
        self._init_status_bar()

        self._ctx.user_changed.connect(self._on_user_changed)
        self._ctx.project_selected.connect(self._on_project_selected)
        self._ctx.collection_changed.connect(self._on_collection_changed)
        self._ctx.collection_list_changed.connect(self._on_collection_list_changed)
        self._ctx.navigate_to.connect(self._on_navigate_to)

        get_http_error_bus().http_error.connect(self._on_http_error)

        if self._ctx.config.token:
            self._load_current_user()
        else:
            self._show_config_dialog()

        self._restore_state()
        self._init_workspace()

        # 自动保存 — 编辑操作触发防抖
        self._auto_saver = _AutoSaveManager(self)
        self._auto_saver.start()
        self._ctx.dirty_changed.connect(self._auto_saver.trigger_debounce)
        self._ctx.dirty_changed.connect(lambda: self._workbench._project_bar.set_save_dirty(True))

    def closeEvent(self, event):
        # 保存持久化状态
        try:
            self._save_current_project()
            self._save_workspace_session()
            if self._ctx.workspace:
                self._ctx.workspace.save()
        except Exception:
            import traceback
            traceback.print_exc()

        if self._assistant_panel and self._assistant_panel.chat._worker:
            w = self._assistant_panel.chat._worker
            if w.isRunning():
                w.cancel()
                w.wait(3000)

        settings = QSettings("TransBridge", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("state", self.saveState())
        super().closeEvent(event)

    def _restore_state(self):
        settings = QSettings("TransBridge", "MainWindow")
        if settings.contains("geometry"):
            self.restoreGeometry(settings.value("geometry"))
        if settings.contains("state"):
            self.restoreState(settings.value("state"))

    # ── Operation cards (hidden, logic-only) ───────────────────

    def _setup_op_cards(self):
        from .workbench.cards.upload_card import UploadCard
        from .workbench.cards.download_card import DownloadCard
        from .workbench.cards.write_card import WriteCard

        self._card_upload = UploadCard(self._ctx, self._op_run_worker, parent=self)
        self._card_upload.setVisible(False)
        self._card_download = DownloadCard(self._ctx, self._op_run_worker, parent=self)
        self._card_download.setVisible(False)
        self._card_write = WriteCard(self._ctx, self._op_run_worker, parent=self)
        self._card_write.setVisible(False)

    # ── Menu ──────────────────────────────────────────────────

    def _init_menu(self):
        mb = self.menuBar()

        # ═══════════════════════════════════════════════════════════
        # 文件菜单
        # ═══════════════════════════════════════════════════════════
        file_menu = mb.addMenu("文件")

        # ── 项目 ──
        self._act_new_project = file_menu.addAction("新建项目…")
        self._act_new_project.triggered.connect(self._on_new_project)
        self._act_open_project = file_menu.addAction("打开项目…")
        self._act_open_project.triggered.connect(self._on_open_project)
        file_menu.addSeparator()

        # ── 解析 ──
        self._act_parse = file_menu.addAction("解析插件…")
        self._act_parse.setShortcut("Ctrl+O")
        self._act_parse.triggered.connect(self._on_parse_plugin)
        self._act_migrate = file_menu.addAction("应用迁移源…")
        self._act_migrate.triggered.connect(self._on_apply_migration)

        file_menu.addSeparator()

        # ── 操作 ──
        self._act_upload = file_menu.addAction("上传至 ParaTranz")
        self._act_upload.triggered.connect(self._on_upload)
        self._act_batch_upload = file_menu.addAction("批量上传…")
        self._act_batch_upload.triggered.connect(self._on_batch_upload)

        file_menu.addSeparator()

        self._act_download = file_menu.addAction("下载合并")
        self._act_download.triggered.connect(self._on_download)
        self._act_batch_download = file_menu.addAction("批量下载…")
        self._act_batch_download.triggered.connect(self._on_batch_download)

        file_menu.addSeparator()

        self._act_write = file_menu.addAction("写回文件…")
        self._act_write.triggered.connect(self._on_write)
        self._act_batch_write = file_menu.addAction("批量写回…")
        self._act_batch_write.triggered.connect(self._on_batch_write)

        file_menu.addSeparator()

        # ── 版本操作 ──
        self._variant_menu = file_menu.addMenu("版本")
        self._act_new_variant = self._variant_menu.addAction("新建版本…")
        self._act_new_variant.triggered.connect(self._on_new_variant)
        self._act_copy_variant = self._variant_menu.addAction("复制当前版本…")
        self._act_copy_variant.triggered.connect(self._on_copy_variant)
        self._variant_menu.addSeparator()
        self._act_save_snapshot = self._variant_menu.addAction("另存为快照…")
        self._act_save_snapshot.triggered.connect(self._on_save_snapshot)
        self._act_load_snapshot = self._variant_menu.addAction("加载快照…")
        self._act_load_snapshot.triggered.connect(self._on_load_snapshot)

        file_menu.addSeparator()

        # ── .transbridge ──
        self._act_export_tb = file_menu.addAction("导出 .transbridge…")
        self._act_export_tb.triggered.connect(self._on_export_transbridge)
        self._act_import_tb = file_menu.addAction("导入 .transbridge…")
        self._act_import_tb.triggered.connect(self._on_import_transbridge)

        file_menu.addSeparator()

        # ── 原有项 ──
        refresh_act = file_menu.addAction("刷新项目列表")
        refresh_act.setShortcut("Ctrl+R")
        refresh_act.triggered.connect(self._refresh_projects)
        file_menu.addSeparator()
        file_menu.addAction("设置 / API 配置").triggered.connect(self._show_config_dialog)
        file_menu.addSeparator()
        quit_act = file_menu.addAction("退出")
        quit_act.setShortcut("Ctrl+Q")
        quit_act.triggered.connect(self.close)

        # ═══════════════════════════════════════════════════════════
        # 小工具菜单
        # ═══════════════════════════════════════════════════════════
        tools_menu = mb.addMenu("小工具")
        self._ai_translator_act = tools_menu.addAction("🤖 AI 自动翻译")
        self._ai_translator_act.triggered.connect(self._open_ai_translator)
        tools_menu.addSeparator()
        self._smart_assistant_act = tools_menu.addAction("💬 智能助手")
        self._smart_assistant_act.setCheckable(True)
        self._smart_assistant_act.setShortcut("Ctrl+Shift+I")
        self._smart_assistant_act.triggered.connect(self._toggle_smart_assistant)

        # ═══════════════════════════════════════════════════════════
        # 视图菜单
        # ═══════════════════════════════════════════════════════════
        view_menu = mb.addMenu("视图")
        self._view_assistant_act = view_menu.addAction("智能助手面板")
        self._view_assistant_act.setCheckable(True)
        self._view_assistant_act.triggered.connect(self._toggle_smart_assistant)

        # ═══════════════════════════════════════════════════════════
        # 账户菜单
        # ═══════════════════════════════════════════════════════════
        acct_menu = mb.addMenu("账户")
        acct_menu.addAction("我的信息").triggered.connect(self._show_user_dialog)
        acct_menu.addAction("私信").triggered.connect(self._show_mails_dialog)

        # ═══════════════════════════════════════════════════════════
        # 帮助菜单
        # ═══════════════════════════════════════════════════════════
        help_menu = mb.addMenu("帮助")
        help_menu.addAction("关于").triggered.connect(self._show_about)

        # 初始状态
        self._update_operation_menu_state()

    # ── Central widget ────────────────────────────────────────

    def _init_central(self):
        self._mode_tabs = QTabWidget()
        self._mode_tabs.setTabPosition(QTabWidget.TabPosition.North)

        self._workbench = WorkbenchWidget(self._ctx)
        self._pt_widget = ParaTranzWidget(self._ctx)

        # 连接 ProjectBar 信号
        pb = self._workbench._project_bar
        pb.new_project_requested.connect(self._on_new_project)
        pb.open_project_requested.connect(self._on_open_project)
        pb.variant_switch_requested.connect(self._switch_variant)
        pb.save_requested.connect(self._on_manual_save)
        pb.variant_add_requested.connect(self._on_new_variant)
        pb.variant_copy_requested.connect(self._on_copy_variant)
        pb.variant_delete_requested.connect(self._on_delete_variant)
        pb.project_rename_requested.connect(self._on_rename_project)

        self._mode_tabs.addTab(self._workbench, "工作台")
        self._mode_tabs.addTab(self._pt_widget, "ParaTranz 管理")

        self.setCentralWidget(self._mode_tabs)

    # ── Status bar ────────────────────────────────────────────

    def _init_status_bar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._user_label = QLabel("未登录")
        self._project_label = QLabel("未选择项目")
        self._api_indicator = _ApiStatusIndicator()
        self._msg_label = QLabel("就绪")

        sb.addPermanentWidget(self._user_label)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self._project_label)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self._api_indicator)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addWidget(self._msg_label)

        bus = get_api_status_bus()
        bus.request_started.connect(self._api_indicator.on_request_started)
        bus.request_finished.connect(self._api_indicator.on_request_finished)

    # ── Context signal handlers ───────────────────────────────

    def _on_http_error(self, status: int, message: str):
        if status == 401:
            self.show_message("Token 已失效，请重新配置")
            self._show_config_dialog()
        elif status == 403:
            self.show_message("权限不足，无法执行此操作")

    def _on_navigate_to(self, index: int):
        self._mode_tabs.setCurrentIndex(index)
        if index == 1:
            self._pt_widget.switch_to_mine()

    def _on_user_changed(self, user):
        if user:
            name = user.get("nickname") or user.get("username") or "已登录"
            self._user_label.setText(f"用户: {name}")
        else:
            self._user_label.setText("未登录")

    def _on_project_selected(self, project):
        if project:
            name = project.get("name", "")
            pid = project.get("id", "")
            self._project_label.setText(f"项目: {name} (id={pid})")
        else:
            self._project_label.setText("未选择项目")
        self._update_operation_menu_state()

    def _on_collection_changed(self, collection):
        if collection:
            self.show_message(f"集合已加载，共 {len(collection)} 条词条")
        self._act_migrate.setEnabled(bool(self._ctx.slots))
        self._update_operation_menu_state()

    def _on_collection_list_changed(self):
        self._act_migrate.setEnabled(bool(self._ctx.slots))
        self._update_operation_menu_state()

    def show_message(self, msg: str):
        self._msg_label.setText(msg)

    # ── Parse actions ─────────────────────────────────────────

    def _on_parse_plugin(self):
        """弹出解析配置对话框，执行后台解析。"""
        from .workbench._parse_config_dialog import ParseConfigDialog
        dlg = ParseConfigDialog(mode="parse", parent=self)
        if dlg.exec() != ParseConfigDialog.DialogCode.Accepted:
            return

        cfg = dlg.get_config()

        if cfg.source_mode == "eet":
            if not cfg.eet_path:
                self.show_message("请先选择 EET XML 文件")
                return
            self._run_parse_eet(cfg)
        else:
            if not cfg.esp_paths:
                self.show_message("请先选择插件文件")
                return
            if len(cfg.esp_paths) > 1:
                self._run_batch_parse_esp(cfg)
            else:
                self._run_parse_esp(cfg)

    def _on_apply_migration(self):
        """弹出迁移源配置对话框，应用到当前集合。"""
        slot = self._ctx.active_slot
        if not slot:
            self.show_message("请先加载集合")
            return

        from .workbench._parse_config_dialog import ParseConfigDialog
        dlg = ParseConfigDialog(mode="migrate", parent=self)
        if dlg.exec() != ParseConfigDialog.DialogCode.Accepted:
            return

        cfg = dlg.get_config()
        if not any([cfg.eet_path, cfg.xt_path, cfg.tp_path, cfg.strings_dir]):
            self.show_message("请先选择迁移源文件")
            return

        self._run_migrate(slot, cfg)

    # ── Parse implementation ──────────────────────────────────

    def _run_parse_esp(self, cfg):
        from src.transbridge.parser.plugin_parser import PluginParser
        from src.transbridge.parser.xt import XT_XmlParser
        from src.transbridge.parser.strings_file import PluginStringsLookup

        esp_path = cfg.esp_paths[0]
        self._workbench.show_step2_progress(0, "解析中…")
        self._workbench.set_step2_parsing(True)

        def _do():
            parser = PluginParser()
            entries = parser.parse_plugin(Path(esp_path), skip_empty=cfg.skip_empty)
            collection = TranslationEntryCollection(entries)
            migrate_count = 0
            if cfg.eet_path:
                try:
                    migrate_count += collection.update_from_eet_xml(Path(cfg.eet_path))
                except Exception:
                    pass
            if cfg.xt_path:
                try:
                    xp = XT_XmlParser.from_file(cfg.xt_path)
                    migrate_count += collection.apply_xt_entries(xp.entries)
                except Exception:
                    pass
            if cfg.tp_path:
                try:
                    migrate_count += collection.update_from_translated_plugin(Path(cfg.tp_path))
                except Exception:
                    pass
            if cfg.strings_dir:
                try:
                    plugin_stem = Path(esp_path).stem
                    strings_lookup = PluginStringsLookup.from_strings_dir(
                        Path(cfg.strings_dir), plugin_stem, cfg.strings_lang
                    )
                    if strings_lookup:
                        migrate_count += collection.update_from_strings_lookup(strings_lookup)
                except Exception:
                    pass
            return collection, migrate_count, parser.get_plugin(), parser.get_strings_lookup()

        def _on_done(result):
            collection, migrate_count, plugin, strings_lookup = result
            self._workbench.hide_step2_progress()
            self._workbench.set_step2_parsing(False)
            label = Path(esp_path).stem
            slot = CollectionSlot(
                label=label,
                collection=collection,
                esp_path=esp_path,
                eet_path=cfg.eet_path,
                xt_path=cfg.xt_path,
                strings_path=cfg.strings_dir,
                strings_lang=cfg.strings_lang,
                migrate_count=migrate_count,
                plugin=plugin,
                strings_lookup=strings_lookup,
            )
            self._finish_parse(esp_path, slot, collection)

        def _on_error(msg: str):
            self._workbench.hide_step2_progress()
            self._workbench.set_step2_parsing(False)
            self.show_message(f"解析失败：{msg}")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _run_batch_parse_esp(self, cfg):
        from src.transbridge.parser.plugin_parser import PluginParser

        esp_paths = cfg.esp_paths
        total = len(esp_paths)
        self._workbench.show_step2_progress(total, f"批量解析中 (0/{total})…")
        self._workbench.set_step2_parsing(True)

        results = []
        current = [0]

        def _parse_next():
            if current[0] >= total:
                _finish_batch()
                return
            esp_path = esp_paths[current[0]]
            self._workbench.update_step2_progress(current[0], total, f"批量解析中 ({current[0] + 1}/{total})…")

            def _do():
                parser = PluginParser()
                entries = parser.parse_plugin(Path(esp_path), skip_empty=cfg.skip_empty)
                collection = TranslationEntryCollection(entries)
                return collection, 0, parser.get_plugin(), parser.get_strings_lookup()

            def _on_one_done(result):
                collection, migrate_count, plugin, strings_lookup = result
                label = Path(esp_path).stem
                slot = CollectionSlot(
                    label=label,
                    collection=collection,
                    esp_path=esp_path,
                    eet_path=None,
                    xt_path=None,
                    strings_path=None,
                    strings_lang=cfg.strings_lang,
                    migrate_count=migrate_count,
                    plugin=plugin,
                    strings_lookup=strings_lookup,
                )
                results.append((esp_path, slot, collection))
                current[0] += 1
                _parse_next()

            def _on_one_error(msg: str):
                results.append((esp_paths[current[0]], None, None))
                current[0] += 1
                _parse_next()

            w = ApiWorker(_do)
            w.result.connect(_on_one_done)
            w.error.connect(_on_one_error)
            w.start()
            self._workers.append(w)

        def _finish_batch():
            self._workbench.hide_step2_progress()
            self._workbench.set_step2_parsing(False)
            success_count = sum(1 for _, slot, _ in results if slot is not None)
            fail_count = total - success_count
            for esp_path, slot, collection in results:
                if slot:
                    self._ctx.add_slot(esp_path, slot)
            if results:
                for esp_path, slot, _ in reversed(results):
                    if slot:
                        self._ctx.activate_slot(esp_path)
                        break
            msg = f"批量解析完成：成功 {success_count} 个"
            if fail_count > 0:
                msg += f"，失败 {fail_count} 个"
            self.show_message(msg)

        _parse_next()

    def _run_parse_eet(self, cfg):
        from src.transbridge.parser.xt import XT_XmlParser

        eet_path = cfg.eet_path
        self._workbench.show_step2_progress(0, "解析 EET 中…")
        self._workbench.set_step2_parsing(True)

        def _do():
            collection = TranslationEntryCollection.from_eet_xml(Path(eet_path))
            migrate_count = 0
            if cfg.xt_path:
                try:
                    xp = XT_XmlParser.from_file(cfg.xt_path)
                    migrate_count += collection.apply_xt_entries(xp.entries)
                except Exception:
                    pass
            if cfg.tp_path:
                try:
                    migrate_count += collection.update_from_translated_plugin(Path(cfg.tp_path))
                except Exception:
                    pass
            return collection, migrate_count

        def _on_done(result):
            collection, migrate_count = result
            self._workbench.hide_step2_progress()
            self._workbench.set_step2_parsing(False)
            label = Path(eet_path).stem
            slot = CollectionSlot(
                label=label,
                collection=collection,
                eet_path=eet_path,
                xt_path=cfg.xt_path,
                migrate_count=migrate_count,
            )
            self._finish_parse(eet_path, slot, collection)

        def _on_error(msg: str):
            self._workbench.hide_step2_progress()
            self._workbench.set_step2_parsing(False)
            self.show_message(f"解析失败：{msg}")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _finish_parse(self, key: str, slot: CollectionSlot, collection):
        if key in self._ctx.slots:
            ret = QMessageBox.question(
                self, "集合已存在",
                f"集合「{slot.label}」已存在，是否覆盖？\n选择「否」将保留原有集合。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                self.show_message("已取消，保留原有集合")
                return
        self._ctx.add_slot(key, slot)
        self.show_message(f"解析完成，共 {len(collection)} 条词条")

    # ── Migration implementation ───────────────────────────────

    def _run_migrate(self, slot, cfg):
        from src.transbridge.parser.xt import XT_XmlParser
        from src.transbridge.parser.strings_file import PluginStringsLookup

        self._workbench.show_step2_progress(0, "应用迁移源中…")

        def _do():
            migrate_count = 0
            updated_slots = []
            apply_all = cfg.strings_apply_all and cfg.strings_dir
            slots_to_process = list(self._ctx.slots.values()) if apply_all else [slot]

            for s in slots_to_process:
                collection = s.collection
                slot_migrate = 0
                if s is slot:
                    if cfg.eet_path and s.eet_path is None:
                        try:
                            slot_migrate += collection.update_from_eet_xml(Path(cfg.eet_path))
                        except Exception:
                            pass
                    if cfg.xt_path and s.xt_path is None:
                        try:
                            xp = XT_XmlParser.from_file(cfg.xt_path)
                            slot_migrate += collection.apply_xt_entries(xp.entries)
                        except Exception:
                            pass
                    if cfg.tp_path:
                        try:
                            slot_migrate += collection.update_from_translated_plugin(Path(cfg.tp_path))
                        except Exception:
                            pass
                if cfg.strings_dir and s.strings_path is None:
                    try:
                        plugin_stem = Path(s.esp_path).stem if s.esp_path else ""
                        strings_lookup = PluginStringsLookup.from_strings_dir(
                            Path(cfg.strings_dir), plugin_stem, cfg.strings_lang
                        )
                        if strings_lookup:
                            slot_migrate += collection.update_from_strings_lookup(strings_lookup)
                            s.strings_lookup = strings_lookup
                    except Exception:
                        pass
                if slot_migrate > 0:
                    updated_slots.append((s, slot_migrate))
                migrate_count += slot_migrate
            return migrate_count, cfg.eet_path, cfg.xt_path, cfg.strings_dir, cfg.strings_lang, updated_slots

        def _on_done(result):
            migrate_count, new_eet, new_xt, new_strings, new_lang, updated_slots = result
            for s, _ in updated_slots:
                if s is slot:
                    if new_eet and s.eet_path is None:
                        s.eet_path = new_eet
                    if new_xt and s.xt_path is None:
                        s.xt_path = new_xt
                if new_strings and s.strings_path is None:
                    s.strings_path = new_strings
                    s.strings_lang = new_lang
            self._workbench.hide_step2_progress()
            if cfg.strings_apply_all and len(updated_slots) > 1:
                self.show_message(f"迁移完成，共 {len(updated_slots)} 个集合，新增 {migrate_count} 条译文")
            else:
                self.show_message(f"迁移完成，新增 {migrate_count} 条译文")
            self._ctx.collection_changed.emit(slot.collection)

        def _on_error(msg: str):
            self._workbench.hide_step2_progress()
            self.show_message(f"迁移失败：{msg}")

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    # ── Operation menu state ──────────────────────────────────

    def _update_operation_menu_state(self):
        has_collection = self._ctx.collection is not None
        project = self._ctx.current_project
        has_project = project is not None

        mine_ids = self._ctx.mine_project_ids
        is_member = (
            not bool(mine_ids)
            or (has_project and project.get("id") in mine_ids)
        )

        self._act_upload.setEnabled(has_collection and has_project and is_member)
        self._act_download.setEnabled(has_collection and has_project and is_member)
        self._act_write.setEnabled(has_collection)

        slots = self._ctx.slots
        multi = len(slots) > 1
        self._act_batch_upload.setVisible(multi)
        self._act_batch_upload.setEnabled(has_project and is_member)
        self._act_batch_download.setVisible(multi)
        self._act_batch_download.setEnabled(has_project and is_member)
        self._act_batch_write.setVisible(multi)
        self._act_batch_write.setEnabled(True)

    # ── Operation actions ─────────────────────────────────────

    def _on_upload(self):
        if not self._ctx.collection or not self._ctx.current_project:
            return
        self._card_upload._do_upload()

    def _on_batch_upload(self):
        if len(self._ctx.slots) <= 1:
            return
        self._card_upload._do_batch_upload()

    def _on_download(self):
        if not self._ctx.collection or not self._ctx.current_project:
            return
        self._card_download._do_download()

    def _on_batch_download(self):
        if len(self._ctx.slots) <= 1:
            return
        self._card_download._do_batch_download()

    def _on_write(self):
        if not self._ctx.collection:
            return
        self._card_write._do_write()

    def _on_batch_write(self):
        if len(self._ctx.slots) <= 1:
            return
        self._card_write._do_batch_write()

    # ── Operation worker helper (proxies to Step2 progress) ────

    def _op_run_worker(self, fn=None, *, fn_factory=None, on_result, on_error,
                       progress_total: int = 0, progress_msg: str = ""):
        """Worker helper: disables menu items, shows Step2 progress, runs background task."""
        ops = [
            self._act_upload, self._act_batch_upload,
            self._act_download, self._act_batch_download,
            self._act_write, self._act_batch_write,
        ]
        saved = [(act, act.isEnabled()) for act in ops]
        for act in ops:
            act.setEnabled(False)

        self._workbench.show_step2_progress(progress_total, progress_msg)

        def _restore():
            self._workbench.hide_step2_progress()
            for act, state in saved:
                act.setEnabled(state)
            self._update_operation_menu_state()

        if fn_factory is not None:
            _cb_ref = [None]
            def _wrapped():
                return fn_factory(_cb_ref[0])
            w = ApiWorker(_wrapped)
            _cb_ref[0] = w.make_progress_callback()
        else:
            w = ApiWorker(fn)

        w.result.connect(on_result)
        w.error.connect(on_error)
        w.progress.connect(self._workbench.update_step2_progress)
        w.finished.connect(_restore)
        w.start()
        self._workers.append(w)

    # ── Existing actions ──────────────────────────────────────

    def _load_current_user(self):
        config = self._ctx.config

        def _fetch():
            api = ParatranzUserAPI(token=config.token, config=config)
            return api.get_my_user()

        def _on_done(u):
            self._ctx.current_user = u
            uid = u.get("id") if isinstance(u, dict) else None
            if uid and config.user_id != uid:
                config.user_id = uid
                config.save_to_file()

        w = ApiWorker(_fetch)
        w.result.connect(_on_done)
        w.error.connect(lambda e: self.show_message(f"获取用户信息失败: {e}"))
        w.start()
        self._workers.append(w)

    def _refresh_projects(self):
        self._pt_widget.refresh_projects()

    def _show_config_dialog(self):
        dlg = ConfigDialog(self._ctx, None)
        dlg.exec()
        if self._ctx.config.token and not self._ctx.current_user:
            self._load_current_user()

    def _show_user_dialog(self):
        if not self._ctx.current_user:
            self.show_message("请先配置 API Token")
            return
        from .paratranz.user_dialog import UserInfoDialog
        UserInfoDialog(self._ctx, self).exec()

    def _show_mails_dialog(self):
        if not self._ctx.current_user:
            self.show_message("请先配置 API Token")
            return
        from .paratranz.mails_dialog import MailsDialog
        MailsDialog(self._ctx, self).exec()

    def _open_ai_translator(self):
        self._workbench.open_tool("ai_translator")

    # ── Smart Assistant ───────────────────────────────────────

    def _init_shortcuts(self):
        self._shortcut_ctrl_k = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut_ctrl_k.activated.connect(self._toggle_smart_assistant)
        self._shortcut_ctrl_s = QShortcut(QKeySequence("Ctrl+S"), self)
        self._shortcut_ctrl_s.activated.connect(self._on_manual_save)

    def _get_assistant_panel(self):
        if self._assistant_panel is None:
            from src.transbridge.ui.tools.smart_assistant import SmartAssistantPanel
            self._assistant_panel = SmartAssistantPanel(self._ctx, self)
            self._assistant_panel.visibility_changed.connect(
                self._on_assistant_visibility_changed
            )
            self.addDockWidget(
                Qt.DockWidgetArea.BottomDockWidgetArea, self._assistant_panel
            )
            self._assistant_panel.hide()
        return self._assistant_panel

    def _toggle_smart_assistant(self):
        panel = self._get_assistant_panel()
        if panel.isVisible():
            panel.hide()
        else:
            panel.show()
            panel.raise_()

    def _on_assistant_visibility_changed(self, visible: bool):
        self._smart_assistant_act.setChecked(visible)
        self._view_assistant_act.setChecked(visible)

    # ── 持久化：工作区管理（S03） ─────────────────────────────

    def _init_workspace(self):
        """启动时读取 workspace.json，恢复上次项目+版本。"""
        ws_path = workspace_path()
        ws = WorkspaceState.load(ws_path)
        self._ctx.workspace = ws

        active = ws.active_project
        if not active or active not in ws.projects:
            self.show_message("就绪 — 无活跃项目，请新建或打开项目")
            return

        proj_path = PathLib(ws.projects[active])
        proj = ProjectHandle.load(proj_path)
        if not proj.name:
            ws.active_project = None
            ws.save()
            self.show_message(f"上次项目「{active}」的配置文件不存在或已损坏")
            return

        self._ctx.active_project = proj
        self._ctx.active_variant = proj.active_variant

        variant_name = proj.active_variant
        vs = None
        if variant_name and proj.has_variant(variant_name):
            vs_path = proj.variant_dir(variant_name) / "current.json"
            vs = VariantStore.load(vs_path)
            self._ctx.variant_store = vs
            if self._ctx.collection:
                count = vs.apply_to(list(self._ctx.collection))
                self.show_message(f"项目「{proj.name}」已恢复，版本「{variant_name}」，恢复 {count} 条译文")

        # 解析项目源文件
        for src in proj.sources:
            if src.get("type") == "esp" and src.get("path"):
                self._restore_parse_esp(src["path"])

        # 异步恢复筛选状态（等待解析完成后应用）
        filter_state = ws.last_session.get("filter_state", {})
        if filter_state:
            QTimer.singleShot(3000, lambda: self._apply_saved_filter_state(filter_state))

    def _save_workspace_session(self):
        """关闭前保存会话状态。"""
        ctx = self._ctx
        ws = ctx.workspace
        if ws is None:
            return
        if ctx.active_project:
            ws.active_project = ctx.active_project.name
            ws.last_session["project"] = ctx.active_project.name
            ws.last_session["variant"] = ctx.active_variant
        # 保存筛选状态
        step2 = getattr(self._workbench, '_step2', None)
        if step2 is not None:
            ws.last_session["filter_state"] = step2.get_filter_state()

    def _apply_saved_filter_state(self, filter_state: dict):
        """恢复持久化的筛选状态。"""
        step2 = getattr(self._workbench, '_step2', None)
        if step2 is not None and filter_state:
            step2.apply_filter_state(filter_state)

    def _on_new_project(self):
        """弹出新建项目对话框。"""
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建项目", "项目名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        proj_dir = PERSISTENCE_ROOT / name
        if proj_dir.exists():
            QMessageBox.warning(self, "冲突", f"项目「{name}」已存在")
            return

        proj = ProjectHandle.create(PERSISTENCE_ROOT, name)
        proj.add_variant("默认")
        proj.active_variant = "默认"
        proj.save()

        # 创建默认版本的 current.json
        from src.transbridge.persistence import VariantStore
        vs = VariantStore(proj.variant_dir("默认") / "current.json")
        vs.save()

        ws = self._ctx.workspace or WorkspaceState.load(workspace_path())
        ws.add_project(name, proj.config_path)
        ws.save()
        self._ctx.workspace = ws
        self._ctx.active_project = proj
        self._ctx.active_variant = "默认"
        self._ctx.variant_store = vs

        QMessageBox.information(
            self, "项目已创建",
            f"项目「{name}」已创建，默认版本「默认」。\n请通过文件菜单解析插件或导入 JSON。"
        )

    def _on_open_project(self):
        """弹出打开项目对话框。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "打开项目文件", str(PERSISTENCE_ROOT),
            "Project JSON (project.json);;所有文件 (*)"
        )
        if not path:
            return

        try:
            proj = ProjectHandle.load(PathLib(path))
        except FileNotFoundError:
            QMessageBox.warning(self, "错误", f"文件不存在: {path}")
            return
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法读取项目文件: {e}")
            return

        # 保存当前项目（如果有）
        self._save_current_project()

        # 加载新项目
        ws = self._ctx.workspace or WorkspaceState.load(workspace_path())
        ws.add_project(proj.name, PathLib(path))
        ws.save()
        self._ctx.workspace = ws
        self._ctx.active_project = proj
        self._ctx.active_variant = proj.active_variant

        variant_name = proj.active_variant
        if variant_name and proj.has_variant(variant_name):
            vs = VariantStore.load(proj.variant_dir(variant_name) / "current.json")
            self._ctx.variant_store = vs
            if self._ctx.collection:
                vs.apply_to(list(self._ctx.collection))

        # 解析源文件
        for src in proj.sources:
            if src.get("type") == "esp" and src.get("path"):
                self._restore_parse_esp(src["path"])

    def _save_current_project(self):
        """保存当前项目的 VariantStore 和 ProjectHandle。"""
        ctx = self._ctx
        if ctx.variant_store and ctx.variant_store.dirty:
            ctx.variant_store.save()
        if ctx.active_project:
            ctx.active_project.save()

    def _restore_parse_esp(self, esp_path: str):
        """后台解析 ESP 源文件（启动恢复用，不阻塞 UI）。"""
        from src.transbridge.parser.plugin_parser import PluginParser

        self._workbench.show_step2_progress(0, f"解析中: {PathLib(esp_path).name}…")

        def _do():
            parser = PluginParser()
            entries = parser.parse_plugin(PathLib(esp_path))
            return TranslationEntryCollection(entries), parser.get_plugin()

        def _on_done(result):
            collection, plugin = result
            self._workbench.hide_step2_progress()
            label = PathLib(esp_path).stem
            slot = CollectionSlot(
                label=label, collection=collection,
                esp_path=esp_path, plugin=plugin,
            )
            self._ctx.add_slot(esp_path, slot)
            # 应用已缓存的翻译数据
            if self._ctx.variant_store:
                self._ctx.variant_store.apply_to(list(collection))

        def _on_error(msg: str):
            self._workbench.hide_step2_progress()

        w = ApiWorker(_do)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    # ── 持久化：版本管理（S04） ─────────────────────────────

    def _on_new_variant(self):
        """创建空白新版本。"""
        proj = self._ctx.active_project
        if not proj:
            return
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建版本", "版本名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if proj.has_variant(name):
            QMessageBox.warning(self, "冲突", f"版本「{name}」已存在")
            return

        self._save_current_project()
        proj.add_variant(name)
        proj.save()
        vs_dir = proj.variant_dir(name)
        vs_dir.mkdir(parents=True, exist_ok=True)
        vs = VariantStore(vs_dir / "current.json")
        vs.save()
        self._switch_to_variant(proj, name, vs)

    def _on_copy_variant(self):
        """从当前版本复制创建新版本。"""
        proj = self._ctx.active_project
        if not proj:
            return
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "复制版本", "新版本名称:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if proj.has_variant(name):
            QMessageBox.warning(self, "冲突", f"版本「{name}」已存在")
            return

        source_name = self._ctx.active_variant or proj.active_variant
        self._save_current_project()

        proj.add_variant(name, copied_from=source_name)
        proj.save()
        vs_dir = proj.variant_dir(name)
        vs_dir.mkdir(parents=True, exist_ok=True)

        # 复制当前版本的 translation + labels
        dest = vs_dir / "current.json"
        new_vs = VariantStore(dest)
        if self._ctx.variant_store:
            new_vs.translations = dict(self._ctx.variant_store.translations)
            new_vs.labels = {k: set(v) for k, v in self._ctx.variant_store.labels.items()}
            new_vs.label_library = dict(self._ctx.variant_store.label_library)
        new_vs.save()
        self._switch_to_variant(proj, name, new_vs)

    def _switch_variant(self, name: str):
        """从 ProjectBar 下拉切换版本。"""
        proj = self._ctx.active_project
        if not proj or not proj.has_variant(name):
            return
        # 检查脏标记
        if self._ctx.variant_store and self._ctx.variant_store.dirty:
            ws = self._ctx.workspace
            behavior = ws.settings.get("save_behavior", "prompt") if ws else "prompt"
            if behavior == "prompt":
                ret = QMessageBox.question(
                    self, "保存确认",
                    f"当前版本「{self._ctx.active_variant}」有未保存的修改。\n是否保存后切换？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                )
                if ret == QMessageBox.StandardButton.Cancel:
                    return
                if ret == QMessageBox.StandardButton.Yes:
                    self._on_manual_save()
            else:
                self._on_manual_save()

        self._save_current_project()
        vs_path = proj.variant_dir(name) / "current.json"
        vs = VariantStore.load(vs_path)
        self._switch_to_variant(proj, name, vs)

    def _on_manual_save(self):
        """手动保存当前版本数据（Ctrl+S / 工具栏按钮）。"""
        ctx = self._ctx
        vs = ctx.variant_store
        if vs is None:
            self.show_message("无活跃版本，无需保存")
            return
        if ctx.collection:
            step2 = getattr(self._workbench, '_step2', None)
            labels, label_lib = step2.collect_labels() if step2 else ({}, {})
            vs.collect_from(list(ctx.collection), labels, label_lib)
        vs.save()
        vs.dirty = False
        self._workbench._project_bar.set_save_dirty(False)
        self._workbench._project_bar.flash_saved()
        total = len(vs.translations)
        labels = sum(1 for s in vs.labels.values() if s)
        self.show_message(f"已保存 — {ctx.active_variant}，{total} 条译文，{labels} 条标签")

    def _switch_to_variant(self, proj: ProjectHandle, name: str, vs: VariantStore) -> None:
        """切换到指定版本并刷新 UI。"""
        self._ctx.active_variant = name
        proj.active_variant = name
        proj.save()
        self._ctx.variant_store = vs
        if self._ctx.collection:
            vs.apply_to(list(self._ctx.collection))
        self._ctx.variant_changed.emit(name)

    def _on_delete_variant(self, name: str):
        """删除指定版本（至少保留一个）。"""
        proj = self._ctx.active_project
        if not proj:
            return
        if len(proj.variants) <= 1:
            QMessageBox.warning(self, "无法删除", "至少保留一个版本。")
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定要删除版本「{name}」吗？\n该版本的所有快照也将被删除，此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 如果删除的是当前版本，先切换到其他版本
        if name == self._ctx.active_variant:
            other = next(v["name"] for v in proj.variants if v["name"] != name)
            self._switch_variant(other)
        # 删除版本目录
        import shutil
        variant_dir = proj.variant_dir(name)
        if variant_dir.exists():
            shutil.rmtree(str(variant_dir))
        proj.remove_variant(name)
        proj.save()
        self._workbench._project_bar.refresh()
        self.show_message(f"已删除版本「{name}」")

    def _on_rename_project(self, new_name: str):
        """重命名项目——移动目录、更新 workspace。"""
        from src.transbridge.persistence._utils import validate_name
        try:
            new_name = validate_name(new_name)
        except ValueError as e:
            QMessageBox.warning(self, "名称无效", str(e))
            return
        proj = self._ctx.active_project
        ws = self._ctx.workspace
        if not proj or not ws:
            return
        old_name = proj.name
        old_dir = proj.project_dir
        new_dir = old_dir.parent / new_name
        if new_dir.exists():
            QMessageBox.warning(self, "冲突", f"项目「{new_name}」已存在")
            return
        # 移动目录
        import shutil
        shutil.move(str(old_dir), str(new_dir))
        # 更新 project.json
        new_config_path = new_dir / "project.json"
        proj._data["name"] = new_name
        proj._path = new_config_path
        proj.save()
        # 更新 workspace
        ws.projects.pop(old_name, None)
        ws.projects[new_name] = str(new_config_path)
        ws.active_project = new_name
        ws.save()
        self._ctx.active_project = proj
        self._workbench._project_bar.refresh()
        self.show_message(f"项目已重命名: {old_name} -> {new_name}")

    # ── 持久化：快照（S06） ──────────────────────────────────

    def _on_save_snapshot(self):
        """另存为快照。"""
        proj = self._ctx.active_project
        vs = self._ctx.variant_store
        if not proj or not vs:
            return
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "另存为快照", "快照名称:")
        if not ok or not name.strip():
            return
        snap_dir = proj.variant_dir(self._ctx.active_variant) / "snapshots"
        dest = vs.save_snapshot(snap_dir, name.strip())
        QMessageBox.information(self, "快照已保存", f"快照已保存到:\n{dest}")

    def _on_load_snapshot(self):
        """加载快照。"""
        proj = self._ctx.active_project
        if not proj:
            return
        variant_name = self._ctx.active_variant or proj.active_variant
        snap_dir = proj.variant_dir(variant_name) / "snapshots"
        snapshots = VariantStore.list_snapshots(snap_dir)
        if not snapshots:
            QMessageBox.information(self, "无快照", "当前版本无可用快照。")
            return

        # 简单列表选择
        items = [f"{s['name']} ({s['updated'][:19]})" for s in snapshots]
        from PyQt6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(self, "加载快照", "选择快照:", items, 0, False)
        if not ok:
            return
        idx = items.index(choice)
        snap_path = PathLib(snapshots[idx]["path"])

        mb = QMessageBox(QMessageBox.Icon.Warning, "确认加载",
                         f"加载快照将覆盖当前版本数据。\n建议先保存当前修改。\n是否继续？",
                         parent=self)
        btn_save = mb.addButton("保存后加载", QMessageBox.ButtonRole.AcceptRole)
        btn_load = mb.addButton("直接加载", QMessageBox.ButtonRole.DestructiveRole)
        btn_cancel = mb.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        mb.exec()
        clicked = mb.clickedButton()
        if clicked == btn_cancel:
            return
        if clicked == btn_save:
            self._on_manual_save()

        vs = VariantStore.load_snapshot(snap_path)
        self._switch_to_variant(proj, variant_name, vs)

    # ── 持久化：.transbridge 导出导入（S07） ──────────────────

    def _on_export_transbridge(self):
        """导出项目为 .transbridge ZIP 文件。"""
        proj = self._ctx.active_project
        if not proj:
            QMessageBox.warning(self, "导出", "请先打开一个项目。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出 .transbridge", f"{proj.name}.transbridge",
            "TransBridge 项目 (*.transbridge);;所有文件 (*)"
        )
        if not path:
            return

        self._save_current_project()
        import zipfile
        proj_dir = proj.project_dir
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in proj_dir.rglob("*.json"):
                arcname = str(f.relative_to(proj_dir))
                zf.write(f, arcname)

        QMessageBox.information(
            self, "导出完成",
            f"项目「{proj.name}」已导出到:\n{path}"
        )

    def _on_import_transbridge(self):
        """导入 .transbridge 文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 .transbridge", "",
            "TransBridge 项目 (*.transbridge);;所有文件 (*)"
        )
        if not path:
            return

        import zipfile
        try:
            with zipfile.ZipFile(path, 'r') as zf:
                # 读取 project.json 获取项目名
                if "project.json" not in zf.namelist():
                    raise ValueError("无效的 .transbridge 文件：缺少 project.json")

                # 检查项目名安全
                from src.transbridge.persistence._utils import validate_name

                # ZIP Slip 防护：逐一检查成员路径
                for member in zf.namelist():
                    member_path = Path(member)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f".transbridge 包含非法路径: {member}")

                # ZIP Bomb 防护：检查解压后总大小
                total_size = sum(info.file_size for info in zf.infolist())
                if total_size > 500 * 1024 * 1024:  # 500MB
                    raise ValueError(
                        f".transbridge 文件解压后过大（{total_size / 1024 / 1024:.0f}MB），"
                        f"超过 500MB 上限"
                    )

                import json, io
                proj_data = json.loads(zf.read("project.json").decode("utf-8"))
                proj_name = proj_data.get("name", "")

                if not proj_name:
                    raise ValueError("project.json 中缺少项目名称")

                proj_name = validate_name(proj_name)

                dest_dir = PERSISTENCE_ROOT / proj_name
                if dest_dir.exists():
                    ret = QMessageBox.question(
                        self, "项目已存在",
                        f"项目「{proj_name}」已存在。\n覆盖将丢失现有数据，是否继续？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if ret != QMessageBox.StandardButton.Yes:
                        return

                # 解压（成员路径已校验安全）
                zf.extractall(dest_dir)
        except Exception as e:
            QMessageBox.warning(self, "导入失败", f"无法导入 .transbridge 文件:\n{e}")
            return

        # 加载导入的项目
        try:
            proj = ProjectHandle.load(dest_dir / "project.json")
            ws = self._ctx.workspace or WorkspaceState.load(workspace_path())
            ws.add_project(proj_name, dest_dir / "project.json")
            ws.save()
            self._ctx.workspace = ws
            self._ctx.active_project = proj
            self._ctx.active_variant = proj.active_variant

            variant_name = proj.active_variant
            if variant_name and proj.has_variant(variant_name):
                vs = VariantStore.load(proj.variant_dir(variant_name) / "current.json")
                self._ctx.variant_store = vs
                if self._ctx.collection:
                    vs.apply_to(list(self._ctx.collection))
        except Exception as e:
            QMessageBox.warning(self, "加载失败", f"项目文件已解压，但加载失败:\n{e}")

        QMessageBox.information(
            self, "导入完成",
            f"项目「{proj_name}」已导入。\n请通过文件菜单解析源文件。"
        )

    def _show_about(self):
        QMessageBox.about(
            self, "关于 TransBridge",
            f"TransBridge v{__version__}\n\nESP 插件翻译辅助工具，对接 ParaTranz 平台。",
        )
