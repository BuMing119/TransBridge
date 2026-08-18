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
from transbridge.paratranz.api.paratranz_user_api import ParatranzUserAPI
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.persistence import WorkspaceState, ProjectHandle, VariantStore, PERSISTENCE_ROOT, workspace_path


def _apply_dictionary_to_collection(collection):
    """解析后自动套用词典，将已有译文填入未翻译条目。

    自动套用走「全词典兜底」（mod_file_id 为空，跳过同名 mod，其余 project/global 全查）。
    只填空译文（不覆盖已有译文）；词典为空或加载失败时静默返回 0，不阻断解析。

    :return: 命中填充的条目数（用于累加到 migrate_count）
    """
    try:
        from transbridge.translation_memory import TranslationMemoryManager
        from transbridge.translation_memory.manager import QueryContext

        manager = TranslationMemoryManager()
        manager.load()

        context = QueryContext(mod_file_id="")
        result = manager.apply_to_collection(collection, context=context)
        return result.applied
    except Exception:  # noqa: BLE001 - 词典不可用时不影响解析
        return 0


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
    """管理自动保存：连续编辑停止后静默保存。"""

    def __init__(self, main_window, parent=None, *, debounce_ms: int = 10_000):
        super().__init__(parent)
        self._mw = main_window
        self._debounce_ms = debounce_ms
        self._interval_timer = QTimer(self)
        self._interval_timer.timeout.connect(self.trigger_debounce)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._auto_save)

    def start(self, interval_minutes: int = 5):
        self._interval_timer.start(interval_minutes * 60000)

    def stop(self):
        self._interval_timer.stop()
        self._debounce_timer.stop()

    def trigger_debounce(self):
        """每次内容变化都重启空闲窗口；clean 状态取消排队保存。"""
        if not self._mw._ctx.dirty:
            self._debounce_timer.stop()
            return
        self._debounce_timer.start(self._debounce_ms)

    def _auto_save(self):
        ctx = self._mw._ctx
        vs = ctx.variant_store
        if ctx.uses_authoritative_projection:
            if not ctx.dirty:
                return
        elif vs is None or not vs.dirty:
            return
        accepted = self._mw._save_current_project_async(automatic=True)
        if not accepted and ctx.dirty:
            self._debounce_timer.start(self._debounce_ms)


class MainWindow(QMainWindow):

    def __init__(self, app_context=None, runtime=None, runtime_context=None):
        super().__init__()
        self.setWindowTitle("TransBridge")
        self.resize(1280, 820)

        # ``ui.app`` is the authoritative path and injects the projection/runtime.
        # The fallback preserves direct historical/test construction until its
        # callers pass the deletion gate.
        self._ctx = app_context if app_context is not None else AppContext(self)
        if (
            app_context is not None
            and hasattr(app_context, "parent")
            and app_context.parent() is None
            and hasattr(app_context, "setParent")
        ):
            app_context.setParent(self)
        self._app_runtime = runtime
        self._runtime_context = runtime_context
        self._project_commands = (
            None if runtime is None else runtime.use_cases.resolve("gui_project_commands")
        )
        self._current_project_opener = (
            None if runtime is None else runtime.use_cases.resolve("current_project_opener")
        )
        self._session_commands = (
            None if runtime is None else runtime.use_cases.resolve("gui_session_commands")
        )
        self._session_projection = (
            None if runtime is None else runtime.use_cases.resolve("session_projection")
        )
        self._legacy_mapping_key: str | None = None
        self._workers: list[ApiWorker] = []
        self._foreground_worker: ApiWorker | None = None
        self._project_open_worker: ApiWorker | None = None
        self._save_worker: ApiWorker | None = None
        self._save_callbacks: list = []
        self._close_pending = False
        self._close_ready = False
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
        self._ctx.dirty_changed.connect(
            lambda: self._workbench._project_bar.set_save_dirty(self._ctx.dirty)
        )

    def closeEvent(self, event):
        if self._close_ready:
            super().closeEvent(event)
            return

        event.ignore()
        if self._close_pending:
            return
        self._close_pending = True
        self._auto_saver.stop()
        self._workbench.show_step2_progress(0, "正在保存并关闭…")

        if self._project_open_worker is not None and self._project_open_worker.isRunning():
            self._project_open_worker.finished.connect(self._begin_background_close)
        elif self._foreground_worker is not None and self._foreground_worker.isRunning():
            self._foreground_worker.finished.connect(self._begin_background_close)
        else:
            self._begin_background_close()

    def _begin_background_close(self) -> None:
        if self._project_open_worker is not None and self._project_open_worker.isRunning():
            self._project_open_worker.finished.connect(self._begin_background_close)
            return
        if self._foreground_worker is not None and self._foreground_worker.isRunning():
            self._foreground_worker.finished.connect(self._begin_background_close)
            return
        if not self._save_current_project_async(on_finished=self._finish_background_close):
            QTimer.singleShot(0, self._begin_background_close)

    def _finish_background_close(self, saved: bool) -> None:
        if not saved:
            self._close_pending = False
            self._workbench.setEnabled(True)
            self._workbench.hide_step2_progress()
            self._auto_saver.start()
            QMessageBox.warning(self, "无法关闭", "项目保存失败，窗口保持打开以避免数据丢失。")
            return
        try:
            self._save_workspace_session()
            if self._ctx.workspace:
                self._ctx.workspace.save()
            settings = QSettings("TransBridge", "MainWindow")
            settings.setValue("geometry", self.saveGeometry())
            settings.setValue("state", self.saveState())
            if self._assistant_panel is not None:
                self._assistant_panel.chat.shutdown(wait_for_worker=False)
            self._ctx.close_projection()
        finally:
            self._workbench.hide_step2_progress()
            self._close_ready = True
            self.close()

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
        tools_menu.addSeparator()
        self._dictionary_act = tools_menu.addAction("📖 翻译词典")
        self._dictionary_act.triggered.connect(self._open_dictionary_panel)
        tools_menu.addSeparator()
        self._fomod_act = tools_menu.addAction("📦 FOMOD 安装包翻译")
        self._fomod_act.triggered.connect(self._open_fomod_panel)

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
        from transbridge.parser.plugin_parser import PluginParser
        from transbridge.parser.xt import XT_XmlParser
        from transbridge.parser.strings_file import PluginStringsLookup

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
            # 自动套用词典（全局词典兜底，填空译文）
            migrate_count += _apply_dictionary_to_collection(collection)
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
        from transbridge.parser.plugin_parser import PluginParser

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
                # 自动套用词典（全局词典兜底，填空译文）
                dict_hits = _apply_dictionary_to_collection(collection)
                return collection, dict_hits, parser.get_plugin(), parser.get_strings_lookup()

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
                    self._save_source_to_project(slot)
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
        from transbridge.parser.xt import XT_XmlParser

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
        self._save_source_to_project(slot)
        self.show_message(f"解析完成，共 {len(collection)} 条词条")

    def _save_source_to_project(self, slot: CollectionSlot) -> None:
        """将解析的源文件路径保存到 project.json（下次启动自动恢复集合）。"""
        proj = self._ctx.active_project
        if proj is None:
            return
        if slot.esp_path and not any(s.get("key") == slot.esp_path for s in proj.sources):
            proj.add_source(slot.esp_path, "esp", slot.esp_path)
        if slot.eet_path and not any(s.get("key") == slot.eet_path for s in proj.sources):
            proj.add_source(slot.eet_path, "eet", slot.eet_path)
        if slot.xt_path and not any(s.get("key") == slot.xt_path for s in proj.sources):
            proj.add_source(slot.xt_path, "xt", slot.xt_path)
        if slot.sst_path and not any(s.get("key") == slot.sst_path for s in proj.sources):
            proj.add_source(slot.sst_path, "sst", slot.sst_path)
        proj.save()

    # ── Migration implementation ───────────────────────────────

    def _run_migrate(self, slot, cfg):
        from transbridge.parser.xt import XT_XmlParser
        from transbridge.parser.strings_file import PluginStringsLookup

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

    def _open_dictionary_panel(self):
        from transbridge.ui.tools.dictionary_panel import DictionaryPanel
        panel = DictionaryPanel(self._ctx, self)
        panel.exec()

    def _open_fomod_panel(self):
        from transbridge.ui.tools.fomod import FomodPanel
        panel = FomodPanel(self._ctx, self)
        panel.exec()

    # ── Smart Assistant ───────────────────────────────────────

    def _init_shortcuts(self):
        self._shortcut_ctrl_k = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut_ctrl_k.activated.connect(self._toggle_smart_assistant)
        self._shortcut_ctrl_s = QShortcut(QKeySequence("Ctrl+S"), self)
        self._shortcut_ctrl_s.activated.connect(self._on_manual_save)

    def _get_assistant_panel(self):
        if self._assistant_panel is None:
            from transbridge.ui.tools.smart_assistant import SmartAssistantPanel
            self._assistant_panel = SmartAssistantPanel(
                self._ctx,
                self,
                session_commands=self._session_commands,
                session_projection=self._session_projection,
                runtime_context=self._runtime_context,
            )
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

        if self._ctx.uses_authoritative_projection:
            if self._current_project_opener is None or self._runtime_context is None:
                self.show_message("当前项目打开服务不可用。")
                return
            from transbridge.application.projects import DirtyDecision

            self._start_current_project_open(
                lambda: self._current_project_opener.prepare_active(self._runtime_context),
                dirty_decision=DirtyDecision.SAVE,
                success_verb="已恢复",
                show_error_dialog=False,
            )
            return

        active = ws.active_project
        if not active or active not in ws.projects:
            self.show_message("就绪 — 无活跃项目，请新建或打开项目")
            return

        proj_path = PathLib(ws.projects[active])
        filter_state = ws.last_session.get("filter_state", {})

        def _prepare_restore():
            project = ProjectHandle.load(proj_path)
            if not project.name:
                raise ValueError(f"上次项目「{active}」的配置文件不存在或已损坏")
            variant_store = None
            variant_name = project.active_variant
            if variant_name and project.has_variant(variant_name):
                variant_store = VariantStore.load(
                    project.variant_dir(variant_name) / "current.json"
                )
            return project, variant_store

        def _activate_restore(result) -> None:
            project, variant_store = result
            self._ctx.active_project = project
            self._ctx.active_variant = project.active_variant
            self._ctx.variant_store = variant_store
            if variant_store is not None and self._ctx.collection:
                count = variant_store.apply_to(list(self._ctx.collection))
                self.show_message(
                    f"项目「{project.name}」已恢复，版本「{project.active_variant}」，"
                    f"恢复 {count} 条译文"
                )
            for source in project.sources:
                if source.get("type") == "esp" and source.get("path"):
                    self._restore_parse_esp(source["path"])
            if filter_state:
                QTimer.singleShot(3000, lambda: self._apply_saved_filter_state(filter_state))

        self._start_foreground_task(
            _prepare_restore,
            message="正在恢复上次项目…",
            on_result=_activate_restore,
            on_error=lambda error: self.show_message(error),
        )

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
        if self._ctx.uses_authoritative_projection:
            self.show_message("Legacy Project creation is disabled under the V2 authority migration gate.")
            return
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
        from transbridge.persistence import VariantStore
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
        from transbridge.persistence.current_project import PROJECT_FILE_FILTER

        initial_directory = (
            str(PERSISTENCE_ROOT)
            if self._current_project_opener is None
            else self._current_project_opener.directory
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目文件",
            initial_directory,
            PROJECT_FILE_FILTER,
        )
        if not path:
            return

        if self._ctx.uses_authoritative_projection:
            if self._current_project_opener is None or self._runtime_context is None:
                self.show_message("当前项目打开服务不可用。")
                return
            dirty_decision = None
            if self._ctx.dirty:
                from transbridge.application.projects import DirtyDecision

                answer = QMessageBox.question(
                    self,
                    "保存确认",
                    "当前项目有未保存修改。打开其他项目前是否保存？",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    return
                dirty_decision = (
                    DirtyDecision.SAVE
                    if answer == QMessageBox.StandardButton.Yes
                    else DirtyDecision.DISCARD
                )
            self._start_current_project_open(
                lambda: self._current_project_opener.prepare_path(path, self._runtime_context),
                dirty_decision=dirty_decision,
                success_verb="已打开",
            )
            return

        def _prepare_legacy_project():
            project = ProjectHandle.load(PathLib(path))
            variant_store = None
            variant_name = project.active_variant
            if variant_name and project.has_variant(variant_name):
                variant_store = VariantStore.load(
                    project.variant_dir(variant_name) / "current.json"
                )
            return project, variant_store

        def _activate_legacy(result) -> None:
            project, variant_store = result
            workspace = self._ctx.workspace or WorkspaceState.load(workspace_path())
            workspace.add_project(project.name, PathLib(path))
            workspace.save()
            self._ctx.workspace = workspace
            self._ctx.active_project = project
            self._ctx.active_variant = project.active_variant
            self._ctx.variant_store = variant_store
            if variant_store is not None and self._ctx.collection:
                variant_store.apply_to(list(self._ctx.collection))
            for source in project.sources:
                if source.get("type") == "esp" and source.get("path"):
                    self._restore_parse_esp(source["path"])

        def _start_legacy_open(saved: bool) -> None:
            if saved:
                self._start_foreground_task(
                    _prepare_legacy_project,
                    message="正在加载项目…",
                    on_result=_activate_legacy,
                    on_error=lambda error: QMessageBox.warning(
                        self,
                        "无法读取项目文件",
                        error,
                    ),
                )

        self._save_current_project_async(on_finished=_start_legacy_open)

    def _start_current_project_open(
        self,
        prepare,
        *,
        dirty_decision,
        success_verb: str,
        show_error_dialog: bool = True,
    ) -> None:
        """Prepare a current Project off the GUI thread, then commit on the GUI thread."""

        if self._project_open_worker is not None and self._project_open_worker.isRunning():
            self.show_message("已有项目正在后台打开，请稍候。")
            return
        if self._save_worker is not None and self._save_worker.isRunning():
            self.show_message("项目仍在保存，请稍候再打开其他项目。")
            return
        self._workbench.show_step2_progress(0, "正在校验项目源文件…")

        def _show_failure(code: str, message: str) -> None:
            self.show_message(f"{code}: {message}")
            if show_error_dialog:
                QMessageBox.warning(self, "无法打开项目", message)

        def _prepare_and_activate():
            prepared = prepare()
            if not prepared.is_success or prepared.value is None:
                return prepared
            return self._current_project_opener.activate(
                prepared.value,
                self._runtime_context,
                dirty_decision=dirty_decision,
            )

        def _on_opened(opened):
            self._workbench.hide_step2_progress()
            if not opened.is_success or opened.value is None:
                diagnostic = opened.diagnostics[0]
                _show_failure(diagnostic.code, diagnostic.message)
                return
            for source in opened.value["sources"]:
                if source.get("type") == "esp" and source.get("path"):
                    self._restore_parse_esp(source["path"])
            self.show_message(f"项目「{opened.value['name']}」{success_verb}")

        def _on_prepare_error(message):
            self._workbench.hide_step2_progress()
            _show_failure("PROJECT_PREPARE_FAILED", message)

        worker = ApiWorker(_prepare_and_activate)
        worker.result.connect(_on_opened)
        worker.error.connect(_on_prepare_error)
        worker.finished.connect(
            lambda: setattr(self, "_project_open_worker", None)
            if self._project_open_worker is worker
            else None
        )
        self._project_open_worker = worker
        worker.start()
        self._workers.append(worker)

    def _start_foreground_task(
        self,
        fn,
        *,
        message: str,
        on_result=None,
        on_error=None,
        on_finished=None,
        disable_workbench: bool = True,
    ) -> bool:
        """Run one visible disk task without blocking the GUI event loop."""

        if (
            self._foreground_worker is not None
            and self._foreground_worker.isRunning()
        ) or (self._save_worker is not None and self._save_worker.isRunning()) or (
            self._project_open_worker is not None and self._project_open_worker.isRunning()
        ):
            self.show_message("另一项后台操作仍在进行，请稍候。")
            return False
        self._workbench.show_step2_progress(0, message)
        if disable_workbench:
            self._workbench.setEnabled(False)
        worker = ApiWorker(fn, route_http_errors=False)
        self._foreground_worker = worker
        outcome: dict[str, object] = {}

        worker.result.connect(lambda result: outcome.__setitem__("result", result))
        worker.error.connect(lambda error: outcome.__setitem__("error", error))

        def _cleanup() -> None:
            if self._foreground_worker is worker:
                self._foreground_worker = None
                self._workbench.hide_step2_progress()
                if disable_workbench:
                    self._workbench.setEnabled(True)
            if "error" in outcome:
                error = str(outcome["error"])
                if on_error is not None:
                    on_error(error)
                else:
                    self.show_message(f"后台操作失败：{error}")
            elif "result" in outcome and on_result is not None:
                on_result(outcome["result"])
            if on_finished is not None:
                on_finished()

        worker.finished.connect(_cleanup)
        worker.start()
        self._workers.append(worker)
        return True

    def _save_current_project(self):
        """同步保存实现；仅允许从后台 worker 调用。"""
        if self._ctx.uses_authoritative_projection:
            if self._project_commands is not None and self._runtime_context is not None:
                return self._project_commands.save(self._runtime_context)
            return None
        ctx = self._ctx
        if ctx.variant_store and ctx.variant_store.dirty:
            ctx.variant_store.save()
        if ctx.active_project:
            ctx.active_project.save()
        return ctx.variant_store

    def _save_current_project_async(self, *, automatic: bool = False, on_finished=None) -> bool:
        """Queue one Project save and coalesce callers while it is active."""

        if self._foreground_worker is not None and self._foreground_worker.isRunning():
            if not automatic:
                self.show_message("另一项后台操作仍在进行，请稍候。")
            return False
        if self._project_open_worker is not None and self._project_open_worker.isRunning():
            if not automatic:
                self.show_message("项目仍在打开，请稍候。")
            return False
        if on_finished is not None:
            self._save_callbacks.append(on_finished)
        if self._save_worker is not None and self._save_worker.isRunning():
            return True

        ctx = self._ctx
        save_fn = self._save_current_project
        if not ctx.uses_authoritative_projection and ctx.variant_store is not None and ctx.collection:
            variant_store = ctx.variant_store
            step2 = getattr(self._workbench, "_step2", None)
            labels, label_library = step2.collect_labels() if step2 else ({}, {})
            entries = list(ctx.collection)
            active_project = ctx.active_project

            def save_fn():
                variant_store.collect_from(entries, labels, label_library)
                variant_store.save()
                if active_project is not None:
                    active_project.save()
                return variant_store

        if not automatic:
            self._workbench.show_step2_progress(0, "正在保存项目…")
            self._workbench.setEnabled(False)
        worker = ApiWorker(save_fn, route_http_errors=False)
        self._save_worker = worker
        save_succeeded = False

        def _on_saved(result) -> None:
            nonlocal save_succeeded
            if hasattr(result, "is_success") and not result.is_success:
                diagnostic = result.diagnostics[0]
                self.show_message(f"{diagnostic.code}: {diagnostic.message}")
                return
            save_succeeded = True
            self._workbench._project_bar.set_save_dirty(False)
            if not automatic:
                self._workbench._project_bar.flash_saved()
            if not automatic:
                self.show_message("项目已保存")

        def _on_error(error: str) -> None:
            self.show_message(f"保存失败：{error}")

        def _on_done() -> None:
            if self._save_worker is worker:
                self._save_worker = None
            if not automatic and self._foreground_worker is None:
                self._workbench.hide_step2_progress()
            if not automatic and not self._close_pending:
                self._workbench.setEnabled(True)
            callbacks, self._save_callbacks = self._save_callbacks, []
            for callback in callbacks:
                callback(save_succeeded)

        worker.result.connect(_on_saved)
        worker.error.connect(_on_error)
        worker.finished.connect(_on_done)
        worker.start()
        self._workers.append(worker)
        return True

    def _restore_parse_esp(self, esp_path: str):
        """后台解析 ESP 源文件（启动恢复用，不阻塞 UI）。"""
        from transbridge.parser.plugin_parser import PluginParser

        self._workbench.show_step2_progress(0, f"解析中: {PathLib(esp_path).name}…")

        def _do():
            parser = PluginParser()
            entries = parser.parse_plugin(PathLib(esp_path))
            if self._ctx.uses_authoritative_projection:
                projection = self._app_runtime.use_cases.resolve("project_projection").snapshot()
                projected = (
                    {}
                    if projection is None
                    else {
                        item["entry_key"]["local_key"]: item
                        for item in projection.to_dict()["values"].get("entries", ())
                    }
                )
                from dataclasses import replace

                entries = [
                    replace(
                        entry,
                        translation=projected[entry.key]["translation"],
                        stage=projected[entry.key]["stage"],
                    )
                    if entry.key in projected
                    else entry
                    for entry in entries
                ]
            return TranslationEntryCollection(entries), parser.get_plugin()

        def _on_done(result):
            collection, plugin = result
            self._workbench.hide_step2_progress()
            # 应用已缓存的翻译数据（必须在 add_slot 之前，否则 collection_changed 触发时表格仍为空）
            if self._ctx.variant_store:
                self._ctx.variant_store.apply_to(list(collection))
            label = PathLib(esp_path).stem
            slot = CollectionSlot(
                label=label, collection=collection,
                esp_path=esp_path, plugin=plugin,
            )
            self._ctx.add_slot(esp_path, slot)

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
        if self._ctx.uses_authoritative_projection:
            from PyQt6.QtWidgets import QInputDialog

            name, ok = QInputDialog.getText(self, "新建版本", "版本名称:")
            if not ok or not name.strip():
                return
            display_name = name.strip()

            def _create() -> None:
                self._start_foreground_task(
                    lambda: self._project_commands.create_variant(
                        display_name,
                        self._runtime_context,
                    ),
                    message=f"正在创建版本「{display_name}」…",
                    on_result=lambda result: self._finish_v2_variant_operation(
                        result,
                        success_message=f"版本「{display_name}」已创建",
                        reload_source=True,
                    ),
                )

            if self._ctx.dirty:
                self._save_current_project_async(on_finished=lambda saved: saved and _create())
            else:
                _create()
            return
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

        def _create_variant():
            proj.add_variant(name)
            proj.save()
            vs_dir = proj.variant_dir(name)
            vs_dir.mkdir(parents=True, exist_ok=True)
            vs = VariantStore(vs_dir / "current.json")
            vs.save()
            return vs

        self._save_current_project_async(
            on_finished=lambda saved: saved
            and self._start_foreground_task(
                    _create_variant,
                    message="正在创建版本…",
                    on_result=lambda vs: self._switch_to_variant(proj, name, vs),
                )
        )

    def _on_copy_variant(self):
        """从当前版本复制创建新版本。"""
        if self._ctx.uses_authoritative_projection:
            from PyQt6.QtWidgets import QInputDialog

            name, ok = QInputDialog.getText(self, "复制版本", "新版本名称:")
            if not ok or not name.strip():
                return
            display_name = name.strip()

            def _copy() -> None:
                self._start_foreground_task(
                    lambda: self._project_commands.create_variant(
                        display_name,
                        self._runtime_context,
                        copy_active=True,
                    ),
                    message=f"正在复制版本「{display_name}」…",
                    on_result=lambda result: self._finish_v2_variant_operation(
                        result,
                        success_message=f"版本「{display_name}」已复制",
                        reload_source=True,
                    ),
                )

            if self._ctx.dirty:
                self._save_current_project_async(on_finished=lambda saved: saved and _copy())
            else:
                _copy()
            return
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
        source_store = self._ctx.variant_store

        def _copy_variant():
            proj.add_variant(name, copied_from=source_name)
            proj.save()
            vs_dir = proj.variant_dir(name)
            vs_dir.mkdir(parents=True, exist_ok=True)
            new_vs = VariantStore(vs_dir / "current.json")
            if source_store:
                new_vs.translations = dict(source_store.translations)
                new_vs.labels = {key: set(value) for key, value in source_store.labels.items()}
                new_vs.label_library = dict(source_store.label_library)
                new_vs.entry_states = dict(source_store.entry_states)
            new_vs.save()
            return new_vs

        self._save_current_project_async(
            on_finished=lambda saved: saved
            and self._start_foreground_task(
                    _copy_variant,
                    message="正在复制版本…",
                    on_result=lambda vs: self._switch_to_variant(proj, name, vs),
                )
        )

    def _switch_variant(self, name: str):
        """从 ProjectBar 下拉切换版本。"""
        if self._ctx.uses_authoritative_projection:
            if self._project_commands is None or self._runtime_context is None:
                self.show_message("V2 项目版本服务不可用。")
                return
            from transbridge.application.projects import DirtyDecision
            from transbridge.persistence.v2 import ProjectId, ProjectRef, VariantId, VariantRef

            project_id = self._ctx.active_project_id
            if project_id is None:
                self.show_message("没有活动项目。")
                return
            display_name = next(
                (str(item["name"]) for item in self._ctx.project_variants if item["id"] == name),
                name,
            )
            decision = None
            if self._ctx.dirty:
                answer = QMessageBox.question(
                    self,
                    "保存确认",
                    "当前版本有未保存修改。切换前是否保存？",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No
                    | QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    self._workbench._project_bar.refresh()
                    return
                if answer == QMessageBox.StandardButton.Yes:
                    self._save_current_project_async(
                        on_finished=lambda saved: (
                            self._switch_variant(name)
                            if saved
                            else self._workbench._project_bar.refresh()
                        )
                    )
                    return
                decision = DirtyDecision.DISCARD
            project_ref = ProjectRef(ProjectId(project_id))
            variant_ref = VariantRef(VariantId(name), project_ref.identity)
            started = self._start_foreground_task(
                lambda: self._project_commands.switch_v2(
                    project_ref,
                    variant_ref,
                    self._runtime_context,
                    dirty_decision=decision,
                ),
                message=f"正在加载版本「{display_name}」…",
                on_result=lambda result: self._finish_v2_variant_operation(
                    result,
                    success_message=f"已切换到版本「{display_name}」",
                    reload_source=True,
                ),
            )
            if not started:
                self._workbench._project_bar.refresh()
            return
        proj = self._ctx.active_project
        if not proj or not proj.has_variant(name):
            return
        should_save = True
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
                should_save = ret == QMessageBox.StandardButton.Yes

        def _load_variant() -> None:
            vs_path = proj.variant_dir(name) / "current.json"
            self._start_foreground_task(
                lambda: VariantStore.load(vs_path),
                message=f"正在加载版本「{name}」…",
                on_result=lambda vs: self._switch_to_variant(proj, name, vs),
            )

        if should_save:
            self._save_current_project_async(on_finished=lambda saved: saved and _load_variant())
        else:
            _load_variant()

    def _on_manual_save(self):
        """手动保存当前版本数据（Ctrl+S / 工具栏按钮）。"""
        if self._ctx.uses_authoritative_projection:
            if self._project_commands is None or self._runtime_context is None:
                self.show_message("V2 Project command adapter is unavailable.")
                return
        elif self._ctx.variant_store is None:
            self.show_message("无活跃版本，无需保存")
            return
        self._save_current_project_async()

    def _activate_legacy_project(self, path: str) -> None:
        project = ProjectHandle.load(PathLib(path))
        if not project.name or not project.active_variant:
            self.show_message("Legacy Project metadata is unavailable or has no active Variant.")
            return
        self._activate_legacy_variant(str(project.config_path), project.active_variant)

    def _activate_legacy_variant(self, project_key: str, variant_name: str) -> None:
        if self._project_commands is None or self._runtime_context is None:
            self.show_message("V2 Project command adapter is unavailable.")
            return
        from transbridge.application.projects import DirtyDecision

        decision = None
        if self._ctx.dirty:
            ret = QMessageBox.question(
                self,
                "保存确认",
                "The active V2 Variant has unpersisted revisions. Save before switching?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                return
            decision = (
                DirtyDecision.SAVE
                if ret == QMessageBox.StandardButton.Yes
                else DirtyDecision.DISCARD
            )
        result = self._project_commands.switch_legacy(
            project_key,
            variant_name,
            self._runtime_context,
            dirty_decision=decision,
        )
        if not result.is_success:
            diagnostic = result.diagnostics[0]
            self.show_message(f"{diagnostic.code}: {diagnostic.message}")
            return
        self._legacy_mapping_key = project_key
        self.show_message("V2 Project/Variant activated after mapping and baseline validation.")

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
        if self._ctx.uses_authoritative_projection:
            display_name = next(
                (str(item["name"]) for item in self._ctx.project_variants if item["id"] == name),
                name,
            )
            if len(self._ctx.project_variants) <= 1:
                QMessageBox.warning(self, "无法删除", "至少保留一个版本。")
                return
            answer = QMessageBox.question(
                self,
                "确认删除",
                f"确定要删除版本「{display_name}」吗？\n此操作不可撤销。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            deleting_active = self._ctx.active_variant_id == name

            def _delete() -> None:
                self._start_foreground_task(
                    lambda: self._project_commands.delete_variant(name, self._runtime_context),
                    message=f"正在删除版本「{display_name}」…",
                    on_result=lambda result: self._finish_v2_variant_operation(
                        result,
                        success_message=f"版本「{display_name}」已删除",
                        reload_source=deleting_active,
                    ),
                )

            if self._ctx.dirty:
                self._save_current_project_async(on_finished=lambda saved: saved and _delete())
            else:
                _delete()
            return
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

    def _finish_v2_variant_operation(
        self,
        result,
        *,
        success_message: str,
        reload_source: bool,
    ) -> None:
        if not result.is_success:
            diagnostic = result.diagnostics[0]
            self.show_message(f"{diagnostic.code}: {diagnostic.message}")
            QMessageBox.warning(self, "版本操作失败", diagnostic.message)
            self._workbench._project_bar.refresh()
            return
        self.show_message(success_message)
        if result.diagnostics:
            self.show_message(f"{success_message}；{result.diagnostics[0].message}")
        if reload_source:
            for source in self._ctx.project_sources:
                if source.get("type") == "esp" and source.get("path"):
                    self._restore_parse_esp(str(source["path"]))

    def _on_rename_project(self, new_name: str):
        """重命名项目——移动目录、更新 workspace。"""
        from transbridge.persistence._utils import validate_name
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
        self._start_foreground_task(
            lambda: vs.save_snapshot(snap_dir, name.strip()),
            message="正在保存快照…",
            on_result=lambda dest: QMessageBox.information(
                self,
                "快照已保存",
                f"快照已保存到:\n{dest}",
            ),
            on_error=lambda error: QMessageBox.warning(self, "快照保存失败", error),
        )

    def _on_load_snapshot(self):
        """加载快照。"""
        proj = self._ctx.active_project
        if not proj:
            return
        variant_name = self._ctx.active_variant or proj.active_variant
        snap_dir = proj.variant_dir(variant_name) / "snapshots"

        def _choose_snapshot(snapshots) -> None:
            if not snapshots:
                QMessageBox.information(self, "无快照", "当前版本无可用快照。")
                return
            items = [f"{item['name']} ({item['updated'][:19]})" for item in snapshots]
            from PyQt6.QtWidgets import QInputDialog

            choice, ok = QInputDialog.getItem(self, "加载快照", "选择快照:", items, 0, False)
            if not ok:
                return
            snap_path = PathLib(snapshots[items.index(choice)]["path"])
            mb = QMessageBox(
                QMessageBox.Icon.Warning,
                "确认加载",
                "加载快照将覆盖当前版本数据。\n建议先保存当前修改。\n是否继续？",
                parent=self,
            )
            btn_save = mb.addButton("保存后加载", QMessageBox.ButtonRole.AcceptRole)
            btn_load = mb.addButton("直接加载", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = mb.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            mb.exec()
            clicked = mb.clickedButton()
            if clicked == btn_cancel:
                return

            def _load() -> None:
                self._start_foreground_task(
                    lambda: VariantStore.load_snapshot(snap_path),
                    message="正在加载快照…",
                    on_result=lambda store: self._switch_to_variant(proj, variant_name, store),
                    on_error=lambda error: QMessageBox.warning(self, "快照加载失败", error),
                )

            if clicked == btn_save:
                self._save_current_project_async(on_finished=lambda saved: saved and _load())
            elif clicked == btn_load:
                _load()

        self._start_foreground_task(
            lambda: VariantStore.list_snapshots(snap_dir),
            message="正在读取快照列表…",
            on_result=_choose_snapshot,
            on_error=lambda error: QMessageBox.warning(self, "快照读取失败", error),
        )

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

        proj_dir = proj.project_dir

        def _export() -> str:
            import zipfile

            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for source in proj_dir.rglob("*.json"):
                    archive.write(source, str(source.relative_to(proj_dir)))
            return path

        def _start_export(saved: bool) -> None:
            if not saved:
                return
            self._start_foreground_task(
                _export,
                message="正在导出项目包…",
                on_result=lambda target: QMessageBox.information(
                    self,
                    "导出完成",
                    f"项目「{proj.name}」已导出到:\n{target}",
                ),
                on_error=lambda error: QMessageBox.warning(self, "导出失败", error),
            )

        self._save_current_project_async(on_finished=_start_export)

    def _on_import_transbridge(self):
        """导入 .transbridge 文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入 .transbridge", "",
            "TransBridge 项目 (*.transbridge);;所有文件 (*)"
        )
        if not path:
            return

        def _inspect_archive():
            import json
            import shutil
            import zipfile

            from transbridge.persistence._utils import validate_name

            with zipfile.ZipFile(path, "r") as archive:
                if "project.json" not in archive.namelist():
                    raise ValueError("无效的 .transbridge 文件：缺少 project.json")
                for member in archive.namelist():
                    member_path = Path(member)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise ValueError(f".transbridge 包含非法路径: {member}")
                total_size = sum(info.file_size for info in archive.infolist())
                project_data = json.loads(archive.read("project.json").decode("utf-8"))
                project_name = validate_name(project_data.get("name", ""))
                destination = PERSISTENCE_ROOT / project_name
                free_bytes = shutil.disk_usage(destination.parent).free
                reserve = max(64 * 1024 * 1024, total_size // 20)
                if total_size + reserve > free_bytes:
                    required_gib = (total_size + reserve) / (1024**3)
                    free_gib = free_bytes / (1024**3)
                    raise ValueError(
                        f"目标磁盘空间不足：至少需要 {required_gib:.1f} GiB，"
                        f"当前可用 {free_gib:.1f} GiB。"
                    )
                return project_name, destination

        def _confirm_and_import(info) -> None:
            project_name, destination = info
            if destination.exists():
                answer = QMessageBox.question(
                    self,
                    "项目已存在",
                    f"项目「{project_name}」已存在。\n覆盖将丢失现有数据，是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return

            def _extract_and_load():
                import zipfile

                with zipfile.ZipFile(path, "r") as archive:
                    archive.extractall(destination)
                project = ProjectHandle.load(destination / "project.json")
                variant_store = None
                variant_name = project.active_variant
                if variant_name and project.has_variant(variant_name):
                    variant_store = VariantStore.load(
                        project.variant_dir(variant_name) / "current.json"
                    )
                return project_name, destination, project, variant_store

            def _activate_imported(result) -> None:
                imported_name, destination, project, variant_store = result
                workspace = self._ctx.workspace or WorkspaceState.load(workspace_path())
                workspace.add_project(imported_name, destination / "project.json")
                workspace.save()
                self._ctx.workspace = workspace
                self._ctx.active_project = project
                self._ctx.active_variant = project.active_variant
                self._ctx.variant_store = variant_store
                if variant_store is not None and self._ctx.collection:
                    variant_store.apply_to(list(self._ctx.collection))
                QMessageBox.information(
                    self,
                    "导入完成",
                    f"项目「{imported_name}」已导入。\n请通过文件菜单解析源文件。",
                )

            self._start_foreground_task(
                _extract_and_load,
                message="正在解压并加载项目包…",
                on_result=_activate_imported,
                on_error=lambda error: QMessageBox.warning(self, "导入失败", error),
            )

        self._start_foreground_task(
            _inspect_archive,
            message="正在校验项目包…",
            on_result=_confirm_and_import,
            on_error=lambda error: QMessageBox.warning(self, "导入失败", error),
        )

    def _show_about(self):
        QMessageBox.about(
            self, "关于 TransBridge",
            f"TransBridge v{__version__}\n\nESP 插件翻译辅助工具，对接 ParaTranz 平台。",
        )

    def _on_report_entry_activated(self, entry_id: str):
        """报告对话框中双击条目后跳转到Step2定位。"""
        if not self._ctx.collection:
            self.statusBar().showMessage("请先加载翻译集合", 5000)
            return
        entry = self._ctx.collection.get(entry_id)
        if entry is None:
            self.statusBar().showMessage(f"条目不存在或已被删除: {entry_id}", 5000)
            return
        # 切换到工作台 tab
        self._mode_tabs.setCurrentIndex(0)  # 工作台在 index 0
        # 通知 Step2 定位
        step2 = getattr(self._workbench, '_step2', None)
        if step2 and hasattr(step2, 'locate_entry'):
            step2.locate_entry(entry_id)
