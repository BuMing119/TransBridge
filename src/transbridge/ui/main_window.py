from pathlib import Path
from typing import List

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel, QMessageBox,
    QFileDialog, QMenu, QProgressBar,
)
from PyQt6.QtCore import Qt, QTimer, QSettings
from PyQt6.QtGui import QShortcut, QKeySequence

from transbridge import __version__
from .context import AppContext, CollectionSlot
from .workers import ApiWorker, get_http_error_bus, get_api_status_bus
from .workbench.widget import WorkbenchWidget
from .paratranz.widget import ParaTranzWidget
from .paratranz.config_dialog import ConfigDialog
from src.transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI
from src.transbridge.converter.translation_entry_collection import TranslationEntryCollection


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

    def closeEvent(self, event):
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
        from src.transbridge.parser.xt_parser import XT_XmlParser
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
        from src.transbridge.parser.xt_parser import XT_XmlParser

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
        from src.transbridge.parser.xt_parser import XT_XmlParser
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

    def _show_about(self):
        QMessageBox.about(
            self, "关于 TransBridge",
            f"TransBridge v{__version__}\n\nESP 插件翻译辅助工具，对接 ParaTranz 平台。",
        )
