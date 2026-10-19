"""
StringDetailDialog：词条详情/编辑对话框。
左侧可折叠筛选 + 可分页词条导航列表，右侧编辑区。
"""

from PyQt6.QtCore import QEvent, QSignalBlocker, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transbridge.paratranz.api.paratranz_strings_api import ParatranzStringsAPI
from transbridge.ui.foundation.adapters import DomainBrushes, ThemeSubscription, ThemeView
from transbridge.ui.foundation.components import ComponentKind, ComponentStyle
from transbridge.ui.foundation.theme_service import ThemeSnapshot

from ..workers import ApiWorker
from ._strings_common import _KEY_ROLE, _STAGE_LABELS, _extract_list
from .string_dialog_lifecycle import StringDialogLifecycle
from .string_dialogs import _SyncTranslationDialog
from .string_edit_session import StringEditSession
from .string_navigation import NavItemDelegate, filtered_indices, sync_candidates


class StringDetailDialog(QDialog):
    """词条详情/编辑对话框：左侧可折叠筛选 + 可分页词条列表，右侧编辑。"""

    strings_updated = pyqtSignal()  # 保存或同步成功后发射，通知外部刷新

    def __init__(
        self,
        strings: list,
        current_index: int,
        ctx,
        project_id: int,
        file_id=None,
        stage=None,
        page: int = 1,
        page_size: int = 50,
        parent=None,
        *,
        theme_view: ThemeView | None = None,
        domain_brushes: DomainBrushes | None = None,
    ):
        super().__init__(parent)
        self._strings = list(strings)
        self._current_idx = current_index
        self._filtered_indices: list[int] = []
        self._ctx = ctx
        self._pid = project_id
        self._file_id = file_id
        self._stage = stage
        self._current_api_stage = stage  # 当前 API 请求使用的 stage（由 UI 筛选动态覆盖）
        self._page = page
        self._page_size = page_size
        self._workers: list[ApiWorker] = []
        self._theme_view = theme_view
        self._domain_brushes = domain_brushes
        self._theme_subscription: ThemeSubscription | None = None
        self._lifecycle = StringDialogLifecycle(self, workers=lambda: tuple(self._workers))
        self._modified = False
        self._nav_gen = 0
        self._busy = False
        self.setWindowTitle("词条详情")
        self.resize(980, 600)
        self._init_ui()
        self._editor = StringEditSession(self, self._trans_edit, self._stage_edit)
        self._accepted_filters = (frozenset(), 0, stage)
        if theme_view is not None:
            self._apply_theme(theme_view.snapshot())
            self._theme_subscription = theme_view.subscribe(self, self._apply_theme)
        self._apply_nav_filter()  # 初始化导航列表

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        splitter.addWidget(self._build_nav_panel())
        splitter.addWidget(self._build_edit_panel())
        splitter.setSizes([240, 720])
        layout.addWidget(splitter)

    def _build_nav_panel(self) -> QWidget:
        nav_widget = QWidget()
        nav_widget.setMinimumWidth(180)
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 0, 4, 0)
        nav_layout.setSpacing(2)

        # ── 折叠筛选面板 ──
        self._filter_toggle = QPushButton("展开筛选")
        self._filter_toggle.setCheckable(True)
        self._filter_toggle.toggled.connect(self._on_filter_toggle)
        nav_layout.addWidget(self._filter_toggle)

        self._filter_panel = self._build_filter_panel()
        self._filter_panel.setVisible(False)
        nav_layout.addWidget(self._filter_panel)

        # ── 导航列表 ──
        self._nav_list = QListWidget()
        self._nav_delegate = NavItemDelegate(self._nav_list, domain_brushes=self._domain_brushes)
        self._nav_list.setItemDelegate(self._nav_delegate)
        self._nav_list.setAccessibleName("词条导航")
        ComponentStyle.apply_static(self._nav_list, ComponentKind.TABLE)
        self._nav_list.currentRowChanged.connect(self._on_nav_row_changed)
        nav_layout.addWidget(self._nav_list, stretch=1)

        # ── 翻页控件 ──
        page_row = QHBoxLayout()
        self._prev_page_btn = QPushButton("上一页")
        self._prev_page_btn.clicked.connect(self._load_prev_page)
        self._page_lbl = QLabel()
        self._page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._page_lbl.setWordWrap(True)
        self._next_page_btn = QPushButton("下一页")
        self._next_page_btn.clicked.connect(self._load_next_page)
        nav_layout.addWidget(self._page_lbl)
        page_row.addWidget(self._prev_page_btn)
        page_row.addStretch()
        page_row.addWidget(self._next_page_btn)
        nav_layout.addLayout(page_row)

        return nav_widget

    def _build_filter_panel(self) -> QFrame:
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        # 翻译状态（多选，不互斥）
        layout.addWidget(QLabel("翻译状态："))
        self._stage_checks: dict[int, QCheckBox] = {}
        stages = [
            (0, "未翻译"),
            (1, "已翻译"),
            (2, "有疑问"),
            (3, "已检查"),
            (5, "已审核"),
            (9, "已锁定"),
            (-1, "已隐藏"),
        ]
        stage_grid = QGridLayout()
        for i, (val, name) in enumerate(stages):
            cb = QCheckBox(name)
            cb.setToolTip(name)
            cb.stateChanged.connect(lambda _: self._on_filter_changed())
            self._stage_checks[val] = cb
            stage_grid.addWidget(cb, i // 2, i % 2)
        layout.addLayout(stage_grid)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        # 修改者（单选，互斥）
        layout.addWidget(QLabel("修改者："))
        self._modifier_group = QButtonGroup(panel)
        self._modifier_group.setExclusive(True)
        mod_row = QVBoxLayout()
        for id_, label in [(0, "全部"), (1, "最后由我修改"), (2, "最后由他人修改")]:
            rb = QRadioButton(label)
            rb.setToolTip(label)
            self._modifier_group.addButton(rb, id_)
            if id_ == 0:
                rb.setChecked(True)
            mod_row.addWidget(rb)
        mod_row.addStretch()
        self._modifier_group.buttonClicked.connect(lambda _: self._apply_nav_filter())
        layout.addLayout(mod_row)

        # 清除筛选
        clear_btn = QPushButton("清除筛选")
        clear_btn.clicked.connect(self._clear_filter)
        layout.addWidget(clear_btn)

        return panel

    def _build_edit_panel(self) -> QWidget:
        right = QWidget()
        right.setMinimumWidth(400)
        right_layout = QVBoxLayout(right)

        form = QFormLayout()
        self._det_key = QLabel()
        self._det_key.setWordWrap(True)

        self._det_orig = QTextEdit()
        self._det_orig.setReadOnly(True)
        self._det_orig.setMinimumHeight(60)
        self._det_orig.setMaximumHeight(150)

        self._det_ctx = QLabel()
        self._det_ctx.setWordWrap(True)
        form.addRow("键名:", self._det_key)
        form.addRow("原文:", self._det_orig)
        form.addRow("上下文:", self._det_ctx)

        self._trans_edit = QTextEdit()
        self._trans_edit.setPlaceholderText("译文（可编辑）")
        self._trans_edit.setMinimumHeight(80)
        form.addRow("译文:", self._trans_edit)

        self._stage_edit = QComboBox()
        for val, name in _STAGE_LABELS.items():
            if val != -2:
                self._stage_edit.addItem(name, val)
        form.addRow("状态:", self._stage_edit)

        right_layout.addLayout(form)
        right_layout.addStretch()

        btn_row = QHBoxLayout()
        self._prev_btn = QPushButton("上一条")
        self._prev_btn.clicked.connect(self._go_prev)
        self._next_btn = QPushButton("下一条")
        self._next_btn.clicked.connect(self._go_next)
        self._save_btn = QPushButton("保存")
        self._save_btn.setFixedWidth(80)
        self._save_btn.clicked.connect(self._save)
        btn_row.addWidget(self._prev_btn)
        btn_row.addWidget(self._next_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        right_layout.addLayout(btn_row)

        return right

    def _apply_theme(self, snapshot: ThemeSnapshot) -> None:
        self._domain_brushes = DomainBrushes(snapshot)
        delegate = getattr(self, "_nav_delegate", None)
        if delegate is not None:
            delegate.apply_domain_brushes(self._domain_brushes)
            self._nav_list.viewport().update()

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.PaletteChange and self._theme_view is None:
            self._nav_delegate.apply_domain_brushes(None)
            self._nav_list.viewport().update()
        super().changeEvent(event)

    def _on_filter_toggle(self, checked: bool):
        self._filter_toggle.setText("收起筛选" if checked else "展开筛选")
        self._filter_panel.setVisible(checked)

    def _on_filter_changed(self):
        """stage 复选框变化时，更新 API stage 参数并从服务端第 1 页重新加载。"""
        if not self._editor.confirm_discard():
            self._restore_filters()
            return
        selected = {v for v, cb in self._stage_checks.items() if cb.isChecked()}
        if len(selected) == 1:
            self._current_api_stage = next(iter(selected))  # 单选 → 服务端过滤
        elif len(selected) == 0:
            self._current_api_stage = self._stage  # 无选 → 恢复主列表原始过滤
        else:
            self._current_api_stage = None  # 多选 → 加载全部，前端再筛
        self._load_page(1)

    def _clear_filter(self):
        for cb in self._stage_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        first_btn = self._modifier_group.button(0)
        if first_btn:
            first_btn.setChecked(True)
        self._on_filter_changed()

    def _get_filtered_indices(self) -> list[int]:
        selected_stages = {v for v, cb in self._stage_checks.items() if cb.isChecked()}
        modifier_id = self._modifier_group.checkedId()
        my_uid = (self._ctx.current_user or {}).get("id")
        return filtered_indices(
            self._strings,
            selected_stages=selected_stages,
            modifier_id=modifier_id,
            current_user_id=my_uid,
        )

    def _apply_nav_filter(self):
        """重新计算筛选结果并刷新导航列表。"""
        indices = self._get_filtered_indices()
        target = self._current_idx if self._current_idx in indices else next(iter(indices), -1)
        record = self._strings[target] if target >= 0 else None
        if record is not self._editor.record and not self._editor.confirm_discard():
            self._restore_filters()
            return
        self._filtered_indices = indices
        self._accepted_filters = (
            frozenset(v for v, cb in self._stage_checks.items() if cb.isChecked()),
            self._modifier_group.checkedId(),
            self._current_api_stage,
        )

        with QSignalBlocker(self._nav_list):
            self._nav_list.clear()
            for fidx in self._filtered_indices:
                s = self._strings[fidx]
                item = QListWidgetItem(s.get("original") or "（无原文）")
                item.setData(_KEY_ROLE, s.get("key", ""))
                orig_preview = (s.get("original") or "")[:200]
                item.setToolTip(f"键名：{s.get('key', '')}\n原文：{orig_preview}")
                self._nav_list.addItem(item)

        # 页码标签
        count, total = len(self._filtered_indices), len(self._strings)
        suffix = f"  {count}/{total} 条" if count != total else f"  {total} 条"
        self._page_lbl.setText(f"第 {self._page} 页{suffix}")
        self._page_lbl.setToolTip("")
        self._show(target)

    def _restore_filters(self):
        stages, modifier, self._current_api_stage = self._accepted_filters
        for value, checkbox in self._stage_checks.items():
            with QSignalBlocker(checkbox):
                checkbox.setChecked(value in stages)
        self._modifier_group.button(modifier).setChecked(True)

    def _on_nav_row_changed(self, nav_row: int):
        if 0 <= nav_row < len(self._filtered_indices):
            self._show(self._filtered_indices[nav_row])

    def _show(self, str_idx: int):
        """显示 self._strings[str_idx] 的内容，并同步导航列表选中状态。"""
        s = self._strings[str_idx] if 0 <= str_idx < len(self._strings) else None
        if s is not self._editor.record and not self._editor.confirm_discard():
            with QSignalBlocker(self._nav_list):
                self._nav_list.setCurrentRow(self._filtered_indices.index(self._current_idx))
            return
        self._current_idx = str_idx if s is not None else -1
        with QSignalBlocker(self._nav_list):
            row = self._filtered_indices.index(str_idx) if str_idx in self._filtered_indices else -1
            self._nav_list.setCurrentRow(row)
        self._editor.bind(s)
        s = s or {}
        self._det_key.setText(s.get("key", "—"))
        self._det_orig.setPlainText(s.get("original", "（无匹配词条）"))
        self._det_ctx.setText(s.get("context") or "—")
        self._update_controls()

    def _update_controls(self):
        idle = not self._busy
        valid = self._current_idx in self._filtered_indices
        pos = self._filtered_indices.index(self._current_idx) if valid else -1
        self._nav_list.setEnabled(idle)
        self._filter_panel.setEnabled(idle)
        self._trans_edit.setEnabled(idle and valid)
        self._stage_edit.setEnabled(idle and valid)
        self._save_btn.setEnabled(idle and valid)
        self._prev_btn.setEnabled(idle and pos > 0)
        self._next_btn.setEnabled(idle and valid and pos < len(self._filtered_indices) - 1)
        self._prev_page_btn.setEnabled(idle and self._page > 1)
        self._next_page_btn.setEnabled(idle and len(self._strings) >= self._page_size)

    def _go_prev(self):
        try:
            pos = self._filtered_indices.index(self._current_idx)
            if pos > 0:
                self._show(self._filtered_indices[pos - 1])
        except ValueError:
            pass

    def _go_next(self):
        try:
            pos = self._filtered_indices.index(self._current_idx)
            if pos < len(self._filtered_indices) - 1:
                self._show(self._filtered_indices[pos + 1])
        except ValueError:
            pass

    # ──────────────────────────────────────────────── 翻页 ──

    def _load_page(self, page: int):
        if self._busy or not self._editor.confirm_discard():
            return
        config = self._ctx.config
        stage = self._current_api_stage
        self._nav_gen += 1
        gen = self._nav_gen

        self._busy = True
        self._update_controls()
        self._page_lbl.setText("加载中…")

        def _fetch():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return _extract_list(
                api.list_strings(
                    self._pid,
                    page=page,
                    page_size=self._page_size,
                    file=self._file_id,
                    stage=stage,
                )
            )

        def _on_done(strings):
            if self._nav_gen != gen:
                return
            self._busy = False
            if not strings and page > self._page:
                self._update_controls()
                self._page_lbl.setText(f"第 {self._page} 页（已是末页）")
                self._prev_page_btn.setEnabled(self._page > 1)
                self._next_page_btn.setEnabled(False)
                return
            self._page = page
            self._strings = strings
            self._apply_nav_filter()

        def _on_error(error):
            if self._nav_gen != gen:
                return
            self._busy = False
            self._restore_filters()
            self._apply_nav_filter()
            self._page_lbl.setText(f"第 {self._page} 页（加载失败，可重试）")
            self._page_lbl.setToolTip(error)

        w = ApiWorker(_fetch, route_http_errors=False)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _load_prev_page(self):
        if self._page > 1:
            self._load_page(self._page - 1)

    def _load_next_page(self):
        self._load_page(self._page + 1)

    # ──────────────────────────────────────────────── 保存 ──

    def _save(self):
        if self._busy or self._current_idx not in self._filtered_indices:
            return
        s = self._strings[self._current_idx]
        new_translation = self._trans_edit.toPlainText()
        new_stage = self._stage_edit.currentData()
        config = self._ctx.config

        self._busy = True
        self._update_controls()
        self._save_btn.setText("保存中…")

        def _do():
            api = ParatranzStringsAPI(token=config.token, config=config)
            return api.update_string(
                self._pid,
                s.get("id"),
                {"translation": new_translation, "stage": new_stage},
            )

        def _on_done(_):
            s["translation"] = new_translation
            s["stage"] = new_stage
            self._modified = True
            self._busy = False
            self._apply_nav_filter()
            if new_stage > 0:
                self._find_sync_candidates(s, new_translation, new_stage)
            else:
                self._update_controls()
                self._save_btn.setText("保存")
                self.strings_updated.emit()
                QMessageBox.information(self, "成功", "保存成功")

        def _on_error(e):
            self._busy = False
            self._update_controls()
            self._save_btn.setText("保存")
            QMessageBox.critical(self, "保存失败", e)

        w = ApiWorker(_do, route_http_errors=False)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def _find_sync_candidates(self, saved_string: dict, new_translation: str, new_stage: int):
        """保存成功后，在本地已加载词条中搜索相同原文的低状态条目，有匹配则提示同步。"""
        current_original = saved_string.get("original", "")
        matches = sync_candidates(self._strings, saved_string, new_stage)

        self._update_controls()
        self._save_btn.setText("保存")

        if not matches:
            self.strings_updated.emit()
            QMessageBox.information(self, "成功", "保存成功")
            return

        dlg = _SyncTranslationDialog(matches, current_original, new_translation, new_stage, self)
        if dlg.exec():
            selected_ids = dlg.get_selected_ids()
            if selected_ids:
                self._do_batch_sync(selected_ids, new_translation, new_stage)
            else:
                self.strings_updated.emit()
                QMessageBox.information(self, "成功", "保存成功")
        else:
            self.strings_updated.emit()
            QMessageBox.information(self, "成功", "保存成功")

    def _do_batch_sync(self, ids: list, translation: str, stage: int):
        config = self._ctx.config
        pid = self._pid

        self._busy = True
        self._update_controls()
        self._save_btn.setText("同步中…")

        def _sync():
            api = ParatranzStringsAPI(token=config.token, config=config)
            for string_id in ids:
                api.update_string(pid, string_id, {"translation": translation, "stage": stage})
            return len(ids)

        def _on_done(_):
            id_set = set(ids)
            for item in self._strings:
                if item.get("id") in id_set:
                    item["translation"] = translation
                    item["stage"] = stage
            self._modified = True
            self._busy = False
            self._apply_nav_filter()
            self._update_controls()
            self._save_btn.setText("保存")
            self.strings_updated.emit()
            QMessageBox.information(self, "成功", f"保存成功，已同步 {len(ids)} 条词条")

        def _on_error(e):
            self._busy = False
            self._update_controls()
            self._save_btn.setText("保存")
            self._apply_nav_filter()
            QMessageBox.warning(self, "同步失败", f"当前词条已保存成功，但批量同步失败：\n{e}")

        w = ApiWorker(_sync, route_http_errors=False)
        w.result.connect(_on_done)
        w.error.connect(_on_error)
        w.start()
        self._workers.append(w)

    def closeEvent(self, event: QCloseEvent):
        event.setAccepted(self._finish_dialog(QDialog.DialogCode.Rejected))

    def reject(self) -> None:
        self.done(QDialog.DialogCode.Rejected)

    def accept(self) -> None:
        self.done(QDialog.DialogCode.Accepted)

    def done(self, result: int) -> None:
        self._finish_dialog(result)

    def _finish_dialog(self, result: int) -> bool:
        lifecycle = getattr(self, "_lifecycle", None)
        if lifecycle is None:
            lifecycle = self._lifecycle = StringDialogLifecycle(
                self,
                workers=lambda: tuple(getattr(self, "_workers", ())),
            )
        if not lifecycle.request_done(result):
            return False
        editor = getattr(self, "_editor", None)
        if editor is not None and not editor.confirm_discard():
            return False
        subscription = getattr(self, "_theme_subscription", None)
        if subscription is not None:
            subscription.close()
            self._theme_subscription = None
        super().done(result)
        return True

    def was_modified(self) -> bool:
        return self._modified
