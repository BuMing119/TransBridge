"""
步骤2：解析结果预览。
显示解析进度、四格统计卡以及全部词条预览表格。
支持多选标签筛选、文本搜索、行内编辑、三态标记（★/?/✓）。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.converter.translation_entry import (
    STAGE_TRANSLATED,
    STAGE_UNTRANSLATED,
    TranslationEntry,
)
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.workbench.entry_menu import build_entry_menu
from transbridge.ui.workbench.filters_presenter import (
    FiltersPresenter,
    FilterState,
    entry_category,
)
from transbridge.ui.workbench.filters_view import FiltersView
from transbridge.ui.workbench.labels_presenter import LabelsPresenter
from transbridge.ui.workbench.labels_view import (
    PRESET_COLORS,
    LabelManagerDialog,
)
from transbridge.ui.workbench.progress_view import ProgressView
from transbridge.ui.workbench.table_presenter import TablePresenter
from transbridge.ui.workbench.translation_reset import TranslationResetAction
from transbridge.ui.workbench.translation_table import (
    COL_KEY as _COL_KEY,
    COL_MARK as _COL_MARK,
    COL_TRANSLATION as _COL_TRANS,
    TranslationTable,
)
from transbridge.ui.workbench.workflow_actions_view import (
    StatisticsSummaryView,
    WorkflowActionsView,
)
from transbridge.ui.workbench.workflow_mixin import WorkflowPresentationMixin
from transbridge.ui.workbench.workflow_presenter import (
    StatisticsSummary,
    WorkbenchWorkflowPresenter,
)

# Compatibility alias used by older internal consumers. New code imports the
# public ``entry_category`` function from filters_presenter.
_entry_category = entry_category


# ────────────────────────────── 步骤2 主 Widget ──────────────────────────────

# 表格列常量由 translation_table 统一定义。
_COL_CHECK = _COL_MARK  # 向后兼容别名

# 标签预设颜色
_PRESET_COLORS = PRESET_COLORS
_LabelManagerDialog = LabelManagerDialog


class Step2PreviewWidget(WorkflowPresentationMixin, QWidget):
    intent_requested = pyqtSignal(str)
    entry_edit_requested = pyqtSignal(object)

    def __init__(self, ctx, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(parent)
        self._ctx = ctx
        self._theme_view = theme_view
        self._labels_presenter = LabelsPresenter(self._commit_labels)
        self._entries: list[TranslationEntry] = []  # 全部词条
        self._category_filters: set[str] = set()  # 多选分类标签
        self._stage_filters: set[int] = set()  # 多选翻译状态标签（0=未翻译,1=有疑问,2=已翻译）
        self._label_library: dict[str, dict] = {}  # label_id → {name, color}
        self._entry_labels: dict[str, set[str]] = {}  # entry_id → set[label_id]
        self._label_filters: set[str] = set()  # 标签筛选
        self._focus_labeled: bool = False  # 只看有标签条目
        self._filtered_total = 0
        self._render_generation = 0
        self._render_entries: tuple[TranslationEntry, ...] = ()
        self._pending_locate_entry_id: str | None = None
        self._filters_presenter = FiltersPresenter()
        self._workflow_presenter = WorkbenchWorkflowPresenter()
        self._summary = StatisticsSummary(0, 0, 0, 0)
        self._tag_buttons: dict[str | int | None, QPushButton] = {}  # 标签按钮
        self._init_ui()
        ctx.collection_changed.connect(self.refresh)
        if getattr(ctx, "uses_authoritative_projection", False):
            ctx.label_data_changed.connect(self._reload_projected_labels)
            self._reload_projected_labels()

    # ── 初始化 UI ────────────────────────────────────────────────────────────

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # 进度条
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(8)
        progress_policy = self._progress.sizePolicy()
        progress_policy.setRetainSizeWhenHidden(True)
        self._progress.setSizePolicy(progress_policy)
        ComponentStyle.apply_static(self._progress, ComponentKind.PROGRESS)
        outer.addWidget(self._progress)

        # 可点击的紧凑摘要；它只改变 FilterState，不直接操作数据。
        self._summary_view = StatisticsSummaryView(self, theme_view=self._theme_view)
        self._summary_view.filter_requested.connect(self._on_summary_filter_requested)
        outer.addWidget(self._summary_view)

        self._filters_view = FiltersView(
            on_changed=self._on_filters_changed,
            on_manage_labels=self._on_manage_labels,
            parent=self,
            theme_view=self._theme_view,
        )
        outer.addWidget(self._filters_view)
        # Compatibility aliases while external consumers migrate to the public
        # filter/selection ports.
        self._category_filters = self._filters_view.category_filters
        self._stage_filters = self._filters_view.stage_filters
        self._label_filters = self._filters_view.label_filters
        self._search_key = self._filters_view.search_key
        self._search_orig = self._filters_view.search_original
        self._search_trans = self._filters_view.search_translation
        self._focus_btn = self._filters_view.focus_button
        self._tags_widget = self._filters_view.category_widget
        self._stage_tags_widget = self._filters_view.stage_widget
        self._mark_tags_widget = self._filters_view.label_widget
        self._search_widget = self._filters_view.search_widget

        # 词条表格（标记列 + 行背景色）
        self._table = TranslationTable(
            on_progress=self._on_render_progress,
            on_batch=self._update_count_label,
            parent=self,
            theme_view=self._theme_view,
        )
        self._table_presenter = TablePresenter(self._table)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        self._table.entry_edit_requested.connect(self.entry_edit_requested.emit)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.selectionModel().selectionChanged.connect(self._on_table_selection_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        self._workflow_actions = WorkflowActionsView(self)
        self._workflow_actions.intent_requested.connect(self.intent_requested.emit)
        self._table_surface = QFrame(self)
        self._table_surface.setObjectName("tbTableSurface")
        table_layout = QVBoxLayout(self._table_surface)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        table_layout.addWidget(self._workflow_actions)
        table_layout.addWidget(self._table, stretch=1)
        outer.addWidget(self._table_surface, stretch=1)

        # 底部计数。表格通过 Qt 事件循环自动增量渲染直至全部完成。
        self._count_lbl = QLabel("已选 0 条 / 共 0 条")
        count_font = self._count_lbl.font()
        count_font.setPointSize(9)
        self._count_lbl.setFont(count_font)
        self._count_lbl.setAccessibleName("Workbench 词条计数")
        outer.addWidget(self._count_lbl)

        # 操作进度区域（解析/上传/下载/写回共用）
        self._progress_view = ProgressView(self)
        outer.addWidget(self._progress_view)
        # Compatibility aliases for existing shell/card integrations.
        self._op_progress = self._progress_view.bar
        self._op_progress_lbl = self._progress_view.label

    # ── 操作进度接口 ──────────────────────────────────────────────────────────

    def show_progress(self, total: int, msg: str = ""):
        self._progress_view.show_progress(total, msg)

    def update_progress(self, current: int, total: int, msg: str):
        self._progress_view.update_progress(current, total, msg)

    def hide_progress(self):
        self._progress_view.hide_progress()

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def get_selected_entries(self) -> list[TranslationEntry]:
        """返回所有有标签的条目，供 AI 翻译浮窗使用。"""
        result = []
        id_to_entry = {e.id: e for e in self._entries if e.id}
        for entry_id in self._entry_labels:
            if entry_id in id_to_entry and self._entry_labels[entry_id]:
                result.append(id_to_entry[entry_id])
        return result

    def selected_entry_ids(self) -> tuple[str, ...]:
        """Return stable IDs for the compatibility 'marked entries' selection."""
        return tuple(entry.id for entry in self.get_selected_entries() if entry.id)

    def selected_row_entry_ids(self) -> tuple[str, ...]:
        """Return actual table-row selection without changing legacy marked-entry scope."""
        return self._table.selected_entry_ids()

    def filtered_entries(self) -> tuple[TranslationEntry, ...]:
        """Return the full filtered projection, independent of rendered batches."""
        return self._render_entries

    def get_filtered_count(self) -> int:
        """返回当前筛选后显示的条数。"""
        return self._filtered_total

    # ── 进度 / 刷新 ───────────────────────────────────────────────────────────

    def set_parsing(self, parsing: bool):
        if parsing:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(100)

    def refresh(self, collection: TranslationEntryCollection | None):
        self._progress.setRange(0, 100)
        if collection is None:
            self._summary = StatisticsSummary(0, 0, 0, 0)
            self._summary_view.set_summary(self._summary)
            self._entries = []
            self._category_filters.clear()
            self._stage_filters.clear()
            self._entry_labels.clear()
            self._label_filters.clear()
            self._focus_labeled = False
            self._filters_view.focus_labeled = False
            self._filters_view.sync_focus_style()
            self._focus_btn.setEnabled(False)
            self._tags_widget.hide()
            self._stage_tags_widget.hide()
            self._mark_tags_widget.hide()
            self._search_widget.hide()
            self._filtered_total = 0
            self._render_entries = ()
            session = self._table_presenter.render((), {}, {})
            self._render_generation = session.generation
            self._update_count_label()
            self._update_workflow_actions()
            return

        self._progress.setValue(100)
        self._entries = list(collection)
        self._summary = StatisticsSummary.from_entries(self._entries)
        self._summary_view.set_summary(self._summary)
        self._category_filters.clear()
        self._stage_filters.clear()

        existing_ids = {e.id for e in collection if e.id}
        self._entry_labels = {eid: ls for eid, ls in self._entry_labels.items() if eid in existing_ids}
        self._label_filters.clear()

        self._build_category_tags()
        self._build_stage_tags()
        self._build_label_tags()
        self._search_widget.show()
        self._populate_table()

    # ── 表格填充 ──────────────────────────────────────────────────────────────

    # ── 标签样式 ────────────────────────────────────────────────────────────

    # ── 分类筛选标签 ────────────────────────────────────────────────────────

    def _build_category_tags(self):
        self._filters_view.build_categories(self._entries)

    def _on_category_tag_clicked(self, category: str | None):
        self._filters_view.toggle_category(category)

    # ── 翻译状态标签 ────────────────────────────────────────────────────────

    # STAGE_LABELS 从 translation_entry 导入，不再本地定义

    def _build_stage_tags(self):
        self._filters_view.build_stages(self._entries)

    def _on_stage_tag_clicked(self, stage: int | None):
        self._filters_view.toggle_stage(stage)

    # ── 标签筛选与管理 ─────────────────────────────────────────────────────

    def _on_manage_labels(self):
        dlg = _LabelManagerDialog(self._label_library, self, theme_view=self._theme_view)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_library = dlg.get_label_library()
            removed = set(self._label_library) - set(new_library)
            new_entry_labels = {key: set(value) for key, value in self._entry_labels.items()}
            for labels in new_entry_labels.values():
                labels.difference_update(removed)
            if not self._commit_labels(new_entry_labels, new_library):
                return
            self._entry_labels = new_entry_labels
            self._label_library = new_library
            self._build_label_tags()
            self._populate_table()

    def _build_label_tags(self):
        self._filters_view.build_labels(
            self._entries,
            self._label_library,
            self._entry_labels,
        )
        self._focus_labeled = self._filters_view.focus_labeled

    def _on_label_tag_clicked(self, lid: str | None):
        self._filters_view.toggle_label(lid)

    def _on_focus_toggle(self):
        self._filters_view.toggle_focus()

    def _on_filters_changed(self) -> None:
        self._focus_labeled = self._filters_view.focus_labeled
        self._build_category_tags()
        self._build_stage_tags()
        self._build_label_tags()
        self._populate_table()

    # ── 表格填充 ──────────────────────────────────────────────────────────────

    def _populate_table(self):
        self._filters_presenter.update(self._filters_view.state())
        self._render_entries = tuple(self._filters_presenter.apply(self._entries, self._entry_labels))
        self._filtered_total = len(self._render_entries)
        projection = getattr(self._ctx, "project_projection", None)
        snapshot = projection.snapshot() if projection is not None else None
        session = self._table_presenter.render(
            self._render_entries,
            self._entry_labels,
            self._label_library,
            projection_revision=getattr(snapshot, "revision", None),
        )
        self._render_generation = session.generation
        if self._pending_locate_entry_id is not None:
            self._table.locate_entry(self._pending_locate_entry_id)
            self._pending_locate_entry_id = None
        self._update_workflow_actions()

    def _on_render_progress(self, current: int, total: int) -> None:
        if total:
            self._progress.setVisible(current < total)
            self._progress.setRange(0, total)
            self._progress.setValue(current)
            return
        self._progress.hide()
        self._progress.setRange(0, 100)
        self._progress.setValue(100)

    def _clear_search(self):
        self._filters_view.clear_search()

    def get_filter_state(self) -> dict:
        """返回当前筛选状态，用于持久化。"""
        return self._filters_view.state().to_mapping()

    def apply_filter_state(self, state: dict) -> None:
        """从持久化状态恢复筛选条件。"""
        if not state:
            return
        self._filters_view.apply_state(FilterState.from_mapping(state))
        self._focus_labeled = self._filters_view.focus_labeled
        self._build_category_tags()
        self._build_stage_tags()
        self._build_label_tags()
        self._populate_table()

    def collect_labels(self) -> tuple[dict[str, set[str]], dict[str, dict]]:
        """返回 (_entry_labels, _label_library) 副本，供持久化保存。"""
        return (
            {key: set(value) for key, value in self._entry_labels.items()},
            {key: dict(value) for key, value in self._label_library.items()},
        )

    # ── 事件处理 ──────────────────────────────────────────────────────────────

    def _on_double_clicked(self, item: QTableWidgetItem):
        """打开词条编辑窗口；未绑定窗口时保留译文行内编辑。"""
        if self._table.activate_entry_editor(item):
            return
        if item.column() == _COL_TRANS:
            self._table.editItem(item)

    def set_editable_entry_keys(self, keys) -> None:
        """Bind popup activation without making Step2 own the editor window."""
        self._table.set_editable_entry_keys(keys)

    def _on_item_changed(self, item: QTableWidgetItem):
        """译文编辑后原地同步 entry、状态文字与行视觉。"""
        if item.column() != _COL_TRANS:
            return
        # Projection commands notify synchronously. A subscriber may rebuild the
        # table during the command, which deletes this QTableWidgetItem wrapper.
        # Capture every value needed from it before crossing that boundary.
        original_row = item.row()
        entry = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(entry, TranslationEntry) or not entry.id:
            return
        new_text = item.text().strip()
        if new_text == "（无译文）":
            new_text = ""
        old_translation = entry.translation
        old_stage = entry.stage
        stage = STAGE_TRANSLATED if new_text and entry.stage == STAGE_UNTRANSLATED else entry.stage
        if getattr(self._ctx, "uses_authoritative_projection", False):
            result = self._ctx.update_projected_entry(
                entry.identity,
                translation=new_text,
                stage=stage,
            )
            if not result.is_success:
                self._populate_table()
                return
        entry.translation = new_text if new_text else ""
        entry.stage = stage
        self._refresh_changed_entry(
            entry,
            preferred_row=original_row,
            old_stage=old_stage,
            translation_filter_may_change=old_translation != entry.translation,
        )

    def _find_rendered_translation_item(
        self,
        preferred_row: int,
        entry_id: str,
    ) -> tuple[int, QTableWidgetItem | None]:
        """Return the current item without dereferencing a deleted Qt wrapper."""
        return self._table.find_translation_item(preferred_row, entry_id)

    # ── 右键菜单 ────────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        menu = self._build_context_menu(row)
        try:
            menu.exec(self._table.viewport().mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def _build_context_menu(self, row: int):
        item = self._table.item(row, _COL_KEY)
        entry = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not isinstance(entry, TranslationEntry) or not entry.id:
            return QMenu(self)
        reset = TranslationResetAction(
            self._ctx, entry, entries=self._entries, selected_ids=self.selected_row_entry_ids(), parent=self
        )

        def cancel_translation():
            if reset.run():
                self._summary = StatisticsSummary.from_entries(self._entries)
                self._summary_view.set_summary(self._summary)
                self._build_stage_tags()
                self._populate_table()

        return build_entry_menu(
            entry,
            label_library=self._label_library,
            assigned_labels=self._entry_labels.get(entry.id, set()),
            on_label_toggle=self._on_label_toggle,
            on_manage_labels=self._on_manage_labels,
            on_create_label=self._on_quick_create_label,
            on_stage_change=lambda selected, stage: self._on_stage_change(selected, stage, preferred_row=row),
            parent=self,
            on_cancel_translation=cancel_translation,
            cancel_translation_enabled=reset.enabled,
        )

    def _on_label_toggle(self, entry_id: str, lid: str, checked: bool):
        entry_labels = self._labels_presenter.toggle(
            self._entry_labels,
            self._label_library,
            entry_id,
            lid,
            checked,
        )
        if entry_labels is None:
            return
        self._entry_labels = entry_labels
        self._build_label_tags()
        self._populate_table()

    def _on_stage_change(self, entry: TranslationEntry, stage_val: int, *, preferred_row: int = -1):
        if entry.stage == stage_val:
            return
        old_stage = entry.stage
        preferred_row = self._table.find_entry_row(preferred_row, entry.id)
        if getattr(self._ctx, "uses_authoritative_projection", False):
            result = self._ctx.update_projected_entry(entry.identity, stage=stage_val)
            if not result.is_success:
                return
        entry.stage = stage_val
        self._refresh_changed_entry(entry, preferred_row=preferred_row, old_stage=old_stage)

    def _refresh_changed_entry(
        self,
        entry: TranslationEntry,
        *,
        preferred_row: int,
        old_stage: int,
        translation_filter_may_change: bool = False,
    ) -> None:
        """Refresh counters and one row; re-filter only when membership may change."""

        self._summary = StatisticsSummary.from_entries(self._entries)
        self._summary_view.set_summary(self._summary)
        self._build_stage_tags()
        stage_membership_changed = bool(self._stage_filters) and (
            (old_stage in self._stage_filters) != (entry.stage in self._stage_filters)
        )
        needs_refilter = stage_membership_changed or (
            translation_filter_may_change
            and bool(self._search_trans.text().strip() or self._filters_view.search_all.text().strip())
        )
        if needs_refilter:
            self._populate_table()
            return
        self._table.update_rendered_entry(entry, preferred_row)
        self._update_count_label()
        self._update_workflow_actions()

    def _on_quick_create_label(self, entry):
        from PyQt6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "新建标签", "标签名称：")
        if not ok or not name.strip():
            return
        result = self._labels_presenter.create(
            self._entry_labels,
            self._label_library,
            entry.id,
            name.strip(),
            tuple(_PRESET_COLORS),
        )
        if result is None:
            return
        entry_labels, library = result
        self._label_library = library
        self._entry_labels = entry_labels
        self._build_label_tags()
        self._populate_table()

    def _commit_labels(
        self,
        entry_labels: dict[str, set[str]],
        label_library: dict[str, dict],
    ) -> bool:
        if not getattr(self._ctx, "uses_authoritative_projection", False):
            return True
        result = self._ctx.replace_projected_labels(entry_labels, label_library)
        return result.is_success

    def _reload_projected_labels(self) -> None:
        self._label_library = self._ctx.label_library
        self._entry_labels = self._ctx.entry_labels
        if hasattr(self, "_filters_view"):
            self._build_label_tags()
        if hasattr(self, "_table"):
            self._populate_table()

    def _update_count_label(self):
        labeled = sum(1 for ls in self._entry_labels.values() if ls)
        shown = self._table.rowCount()
        filtered = self._filtered_total
        total = len(self._entries)

        if filtered == total and shown == total:
            self._count_lbl.setText(f"有标签 {labeled} 条 | 共 {total} 条")
        elif shown < filtered:
            self._count_lbl.setText(f"有标签 {labeled} 条 | 已加载 {shown} 条（筛选结果 {filtered} 条，共 {total} 条）")
        else:
            self._count_lbl.setText(f"有标签 {labeled} 条 | 筛选结果 {filtered} 条（共 {total} 条）")

    def locate_entry(self, entry_id: str):
        """在表格中定位到指定条目（清除筛选，滚动到行并选中）。"""
        # 清除所有筛选以便目标行可见
        self._filters_view.apply_state(FilterState())
        self._focus_labeled = False
        self._filters_view.sync_focus_style()

        # 更新标签UI
        self._build_category_tags()
        self._build_stage_tags()
        if hasattr(self, "_build_label_tags"):
            self._build_label_tags()

        # 刷新表格
        self._pending_locate_entry_id = entry_id
        self._populate_table()

    def closeEvent(self, event) -> None:
        """Release owned debounce and incremental-render callbacks before hide."""
        self._filters_view.search_timer.stop()
        self._table.close_rendering()
        super().closeEvent(event)
