"""
WorkbenchWidget: 工作台布局（集合工具栏 + 词条预览全宽）。
Story-18: Step1/Step3 面板已移除。
Story-19: 左侧 CollectionStatsPanel 已移除，分类统计以标签组形式嵌入 Step2。
"""

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.guidance.qt import GuidanceBanner
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.windowing import show_and_activate

from ._project_bar import ProjectBar
from .remote_target_view import RemoteTargetView
from .step2 import Step2PreviewWidget
from .workflow_presenter import WorkbenchHierarchyViewState, WorkbenchWorkflowPresenter


def _track_ai_progress(tool_windows: dict, progress_win) -> None:
    tool_windows["ai_translator_progress"] = progress_win
    tool_windows.pop("ai_translator", None)
    show_and_activate(progress_win, deferred=True)


class WorkbenchWidget(QWidget):
    intent_requested = pyqtSignal(str)

    def __init__(self, ctx, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(parent)
        self.setObjectName("tbWorkbench")
        self.setAccessibleName("翻译工作台")
        self._initial_focus_set = False
        self._ctx = ctx
        self._theme_view = theme_view
        self._tool_windows: dict = {}
        self._workflow_presenter = WorkbenchWorkflowPresenter()
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        context_card = QFrame(self)
        context_card.setAccessibleName("当前翻译上下文")
        ComponentStyle.apply_static(context_card, ComponentKind.CARD)
        context_layout = QHBoxLayout(context_card)
        context_layout.setContentsMargins(10, 6, 10, 6)
        context_layout.setSpacing(12)

        # ── 项目与翻译内容上下文 ───────────────────────────
        self._project_bar = ProjectBar(self._ctx, theme_view=self._theme_view)
        self._project_bar.snapshot_save_requested.connect(
            lambda: self.intent_requested.emit(IntentId.PROJECT_SNAPSHOT_SAVE.value)
        )
        self._project_bar.snapshot_load_requested.connect(
            lambda: self.intent_requested.emit(IntentId.PROJECT_SNAPSHOT_LOAD.value)
        )
        context_layout.addWidget(self._project_bar, 1)

        self._remote_target = RemoteTargetView(self._ctx, self)
        context_layout.addWidget(self._remote_target, 1)

        self._collection_bar = self._build_collection_bar()
        context_layout.addWidget(self._collection_bar, 1)
        layout.addWidget(context_card)

        self._guidance_banner = GuidanceBanner(self)
        layout.addWidget(self._guidance_banner)

        self._step2 = Step2PreviewWidget(self._ctx, theme_view=self._theme_view)
        self._step2.intent_requested.connect(self.intent_requested.emit)
        layout.addWidget(self._step2)

        self._ctx.collection_list_changed.connect(self._rebuild_collection_combo)
        self._rebuild_collection_combo()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._initial_focus_set:
            return
        self._initial_focus_set = True
        self._collection_combo.setFocus()

    @property
    def project_bar(self) -> ProjectBar:
        """Public shell integration point; callers need not reach into layout state."""
        return self._project_bar

    @property
    def preview(self) -> Step2PreviewWidget:
        """Return the stable Step2 facade, not its private table implementation."""
        return self._step2

    @property
    def hierarchy_state(self) -> WorkbenchHierarchyViewState:
        return self._project_bar.hierarchy_state

    @property
    def guidance_banner(self) -> GuidanceBanner:
        return self._guidance_banner

    def collect_labels(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
        return self._step2.collect_labels()

    def selected_entry_ids(self) -> tuple[str, ...]:
        return self._step2.selected_entry_ids()

    def selected_row_entry_ids(self) -> tuple[str, ...]:
        return self._step2.selected_row_entry_ids()

    def filtered_entries(self):
        return self._step2.filtered_entries()

    def locate_entry(self, entry_id: str) -> None:
        self._step2.locate_entry(entry_id)

    def open_management_menu(self) -> None:
        """Open the secondary management surface for the canonical intent."""

        self._manage_button.showMenu()

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

    def open_tool(self, tool_id: str, *, task_runtime=None, settings_requested=None):
        if tool_id == "ai_batch_translation":
            tool_id = "ai_translator"

        if tool_id == "ai_translator":
            progress_win = self._tool_windows.get("ai_translator_progress")
            if progress_win is not None and progress_win.is_running():
                show_and_activate(progress_win)
                return

            from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow

            win = AITranslatorWindow.open_for_translation(
                self._ctx,
                self._step2,
                parent=self,
                task_runtime=task_runtime,
                theme_view=self._theme_view,
                settings_requested=settings_requested,
            )
            if win is None:
                return

            if isinstance(win, AITranslatorWindow):
                win.progress_window_created.connect(self._on_progress_window_created)
                self._tool_windows["ai_translator"] = win
            else:
                _track_ai_progress(self._tool_windows, win)
            return

        if tool_id in self._tool_windows:
            show_and_activate(self._tool_windows[tool_id])

    def _on_progress_window_created(self, progress_win):
        # The signal is emitted while the configuration window is still active.
        # Defer activation until that window's start handler has closed it.
        _track_ai_progress(self._tool_windows, progress_win)

    # ── Collection toolbar ───────────────────────────────────

    def _build_collection_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        row.addWidget(QLabel("翻译内容 ·"))

        self._collection_combo = QComboBox()
        self._collection_combo.setAccessibleName("当前翻译内容")
        self._collection_combo.setMinimumWidth(180)
        self._collection_combo.setMinimumContentsLength(16)
        self._collection_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ComponentStyle.apply_static(self._collection_combo, ComponentKind.INPUT)
        self._collection_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._collection_combo.currentIndexChanged.connect(self._on_collection_switch)
        row.addWidget(self._collection_combo, stretch=1)

        self._btn_import = QPushButton("导入已有译文…")
        self._btn_import.setAccessibleName("导入已有译文")
        self._btn_import.setFlat(True)
        ComponentStyle.apply_static(self._btn_import, ComponentKind.BUTTON)
        self._btn_import.setToolTip("从 JSON 文件导入翻译集合")
        self._btn_import.clicked.connect(self._on_import_json)
        row.addWidget(self._btn_import)

        self._manage_button = QToolButton()
        self._manage_button.setAccessibleName("管理当前翻译内容")
        self._manage_button.setText("管理 ▾")
        self._manage_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        ComponentStyle.apply_static(self._manage_button, ComponentKind.BUTTON)
        manage_menu = QMenu(self._manage_button)
        self._btn_terminology = manage_menu.addAction("构建术语库…")
        self._btn_terminology.setToolTip("构建、检查、调整并发布当前工程的术语库")
        self._btn_terminology.triggered.connect(
            lambda: self.intent_requested.emit(IntentId.TERMINOLOGY_WORKBENCH.value)
        )
        manage_menu.addSeparator()
        self._btn_new = manage_menu.addAction("为当前工程添加插件…")
        self._btn_new.setToolTip("选择插件并加载为当前工程的新翻译内容")
        self._btn_new.triggered.connect(self._on_new_slot)
        manage_menu.addSeparator()
        self._btn_remove = manage_menu.addAction("移除当前翻译内容…")
        self._btn_remove.setToolTip("移除当前翻译内容（不删除源文件）")
        self._btn_remove.triggered.connect(self._on_remove_slot)
        self._manage_button.setMenu(manage_menu)
        row.addWidget(self._manage_button)

        return bar

    def _rebuild_collection_combo(self):
        """根据 ctx.slots 重建下拉列表。无集合时禁用控件。"""
        slots = self._ctx.slots
        active = self._ctx.active_key

        self._collection_combo.blockSignals(True)
        try:
            self._collection_combo.clear()
            if not slots:
                sources = getattr(self._ctx, "project_sources", ())
                if sources:
                    for source in sources:
                        state = self._workflow_presenter.hierarchy(
                            project_id=getattr(self._ctx, "active_project_id", None),
                            project_name=getattr(self._ctx, "project_name", None),
                            variant_id=getattr(self._ctx, "active_variant_id", None),
                            variant_name=self._active_variant_name(),
                            sources=(source,),
                        )
                        self._collection_combo.addItem(state.content_label, state.identity)
                        self._collection_combo.setItemData(
                            self._collection_combo.count() - 1,
                            state.content_label,
                            Qt.ItemDataRole.ToolTipRole,
                        )
                else:
                    self._collection_combo.addItem("（无翻译内容）")
                self._collection_combo.setEnabled(False)
                self._btn_remove.setEnabled(False)
                return

            self._collection_combo.setEnabled(True)
            self._btn_remove.setEnabled(True)
            active_idx = 0
            for i, (key, slot) in enumerate(slots.items()):
                label = self._content_display_label(key, slot)
                self._collection_combo.addItem(label, key)
                self._collection_combo.setItemData(i, label, Qt.ItemDataRole.ToolTipRole)
                if key == active:
                    active_idx = i
            self._collection_combo.setCurrentIndex(active_idx)
        finally:
            self._collection_combo.blockSignals(False)

    @staticmethod
    def _content_display_label(key: str, slot) -> str:
        name = slot.label or (Path(key).name if key else "未命名内容")
        if getattr(slot, "plugin", None) is not None or getattr(slot, "esp_path", None):
            return f"插件 · {name}"
        if getattr(slot, "strings_path", None):
            return f"本地化字符串 · {name}"
        return f"翻译内容 · {name}"

    def _active_variant_name(self) -> str | None:
        active = getattr(self._ctx, "active_variant_id", None)
        for value in getattr(self._ctx, "project_variants", ()):
            if str(value.get("id")) == str(active):
                return str(value.get("name") or active)
        return None

    def _on_collection_switch(self, index: int):
        key = self._collection_combo.itemData(index)
        if key and key != self._ctx.active_key:
            self._ctx.activate_slot(key)

    def _on_new_slot(self):
        self.intent_requested.emit(IntentId.WORKBENCH_CONTENT_PREPARE.value)

    def _on_import_json(self):
        from transbridge.converter.translation_entry_collection import (
            TranslationEntryCollection,
        )
        from transbridge.ui.context import CollectionSlot
        from transbridge.ui.workers import ApiWorker

        path, _ = QFileDialog.getOpenFileName(self, "导入 JSON 文件", "", "JSON 文件 (*.json);;所有文件 (*)")
        if not path:
            return

        self.show_step2_progress(0, "加载 JSON 中…")

        def _do():
            return TranslationEntryCollection.from_json_file(path)

        def _on_done(collection):
            self.hide_step2_progress()
            if self._ctx.uses_authoritative_projection:
                from dataclasses import replace

                target = self._ctx.active_slot
                if target is None:
                    QMessageBox.warning(self, "无法导入", "请先选择要更新的翻译内容。")
                    return
                exact = {entry.identity: entry for entry in collection}
                by_local: dict[str, list] = {}
                for entry in collection:
                    by_local.setdefault(entry.identity.local_key, []).append(entry)
                states = {}
                replacements = {}
                for entry in target.collection:
                    imported = exact.get(entry.identity)
                    if imported is None:
                        candidates = by_local.get(entry.identity.local_key, ())
                        imported = candidates[0] if len(candidates) == 1 else None
                    if imported is None:
                        continue
                    states[entry.identity] = (imported.translation, imported.stage)
                    replacements[entry.identity] = imported
                if not states:
                    QMessageBox.warning(self, "无法导入", "导入文件中没有可映射到当前来源的条目。")
                    return
                committed = self._ctx.project_commands.replace_entry_states(
                    states,
                    self._ctx.runtime_context,
                )
                if not committed.is_success:
                    diagnostic = committed.diagnostics[0]
                    QMessageBox.warning(self, "导入失败", diagnostic.message)
                    return
                target.collection = TranslationEntryCollection(
                    replace(
                        entry,
                        translation=replacements[entry.identity].translation,
                        stage=replacements[entry.identity].stage,
                    )
                    if entry.identity in replacements
                    else entry
                    for entry in target.collection
                )
                self._ctx.collection_changed.emit(target.collection)
                return
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
        if self._ctx.uses_authoritative_projection:
            scope = (
                "范围：从当前工程移除此内容、译文状态，以及已合并的汉化来源登记，不删除磁盘上的源文件。\n"
                "保存后重新打开工程，此内容也不会恢复。重新导入源文件不能保证恢复工程内的译文编辑。"
            )
        else:
            scope = "范围：仅从当前工作台移除已解析数据，不删除源文件。\n恢复方式：之后可重新导入同一来源。"
        ret = QMessageBox.question(
            self,
            "移除集合",
            f"确定要移除翻译内容「{label}」吗？\n{scope}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            if self._ctx.uses_authoritative_projection:
                removed = self._ctx.project_commands.remove_source(active, self._ctx.runtime_context)
                if not removed.is_success:
                    diagnostic = removed.diagnostics[0]
                    message = (
                        "移除未能完成，工程内容未改变。请查看日志中的错误详情。"
                        if diagnostic.code == "ACTIVE_CONTENT_CHANGE_FAILED"
                        else diagnostic.message
                    )
                    QMessageBox.warning(self, "移除失败", message)
                    return
            self._ctx.remove_slot(active)
