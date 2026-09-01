"""项目工具栏：项目名/版本切换/保存/管理按钮。"""

from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton, QSizePolicy, QWidget

from transbridge.ui.foundation.adapters import DomainBrushes, ThemeView
from transbridge.ui.foundation.components import (
    ComponentKind,
    ComponentStyle,
    ElidedLabel,
    SemanticState,
    make_primary_button,
    reserve_text_width,
)
from transbridge.ui.foundation.tabler_icons import tabler_icon
from transbridge.ui.workbench.save_presenter import SavePhase, SaveStatePresenter, SaveTarget, SaveViewState
from transbridge.ui.workbench.workflow_presenter import WorkbenchHierarchyViewState, WorkbenchWorkflowPresenter


class ProjectBar(QWidget):
    """工作台顶部项目工具栏——项目+版本选择+保存+管理按钮。"""

    new_project_requested = pyqtSignal()
    open_project_requested = pyqtSignal()
    variant_switch_requested = pyqtSignal(str)  # variant_name
    save_requested = pyqtSignal()
    variant_add_requested = pyqtSignal()
    variant_copy_requested = pyqtSignal()
    variant_delete_requested = pyqtSignal(str)  # variant_name
    project_rename_requested = pyqtSignal(str)  # new_name
    project_delete_requested = pyqtSignal(str, str)  # Project ID, exact displayed name
    snapshot_save_requested = pyqtSignal()
    snapshot_load_requested = pyqtSignal()
    snapshot_delete_requested = pyqtSignal()

    def __init__(self, ctx, parent=None, *, theme_view: ThemeView | None = None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._ctx = ctx
        self._domain: DomainBrushes | None = None
        self._domain_factory = DomainBrushes if theme_view is None else theme_view.domain_brushes
        self._workflow_presenter = WorkbenchWorkflowPresenter()
        self._save_presenter = SaveStatePresenter()
        self._hierarchy_state = self._workflow_presenter.hierarchy(
            project_id=None,
            project_name=None,
            variant_id=None,
            variant_name=None,
            sources=(),
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(4)

        # ── 项目名（可编辑） ──
        layout.addWidget(QLabel("本地工程 ·"))
        self._project_label = ElidedLabel("无项目")
        self._project_label.setAccessibleName("当前本地翻译工程")
        project_font = self._project_label.font()
        project_font.setBold(True)
        self._project_label.setFont(project_font)
        layout.addWidget(self._project_label, 1)

        self._rename_btn = QPushButton("...")
        self._rename_btn.setFixedWidth(28)
        self._rename_btn.setFlat(True)
        ComponentStyle.apply_static(self._rename_btn, ComponentKind.BUTTON)
        self._rename_btn.setToolTip("管理本地工程")
        self._rename_btn.setAccessibleName("管理本地翻译工程")
        self._rename_btn.clicked.connect(self._on_project_menu)
        layout.addWidget(self._rename_btn)

        # ── 版本下拉 ──
        layout.addWidget(QLabel(" 翻译版本 ·"))
        self._variant_combo = QComboBox()
        self._variant_combo.setAccessibleName("当前翻译版本")
        self._variant_combo.setMinimumWidth(100)
        self._variant_combo.setMinimumContentsLength(8)
        self._variant_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._variant_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        ComponentStyle.apply_static(self._variant_combo, ComponentKind.INPUT)
        self._variant_combo.currentIndexChanged.connect(self._on_variant_selected)
        layout.addWidget(self._variant_combo)

        # ── 版本管理按钮 ──
        self._variant_menu_btn = QPushButton()
        self._variant_menu_btn.setFixedWidth(28)
        self._variant_menu_btn.setIconSize(QSize(16, 16))
        self._variant_menu_btn.setFlat(True)
        ComponentStyle.apply_static(self._variant_menu_btn, ComponentKind.BUTTON)
        self._variant_menu_btn.setToolTip("管理版本")
        self._variant_menu_btn.setAccessibleName("管理翻译版本")
        self._variant_menu_btn.clicked.connect(self._on_variant_menu)
        self._refresh_variant_icon()
        layout.addWidget(self._variant_menu_btn)

        # ── 保存按钮 ──
        self._save_btn = make_primary_button("保存")
        self._save_btn.setAccessibleName("保存当前翻译版本")
        self._save_btn.setToolTip("保存当前版本的翻译数据 (Ctrl+S)")
        save_font = self._save_btn.font()
        save_font.setBold(True)
        self._save_btn.setFont(save_font)
        self._save_btn.clicked.connect(self.save_requested.emit)
        reserve_text_width(self._save_btn, ("保存", "已保存", "重试保存"))
        layout.addWidget(self._save_btn)

        self._save_status = ElidedLabel("未打开工程")
        self._save_status.setAccessibleName("保存状态")
        self._save_status.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        status_font = self._save_status.font()
        status_font.setPointSize(9)
        self._save_status.setFont(status_font)
        layout.addWidget(self._save_status, 1)

        layout.addStretch()

        # ── 项目管理按钮 ──
        self._btn_new = QPushButton("+ 新建项目")
        self._btn_new.setAccessibleName("新建本地翻译工程")
        self._btn_new.setFlat(True)
        ComponentStyle.apply_static(self._btn_new, ComponentKind.BUTTON)
        self._btn_new.clicked.connect(self.new_project_requested.emit)
        layout.addWidget(self._btn_new)

        self._btn_open = QPushButton("打开项目")
        self._btn_open.setAccessibleName("打开本地翻译工程")
        self._btn_open.setFlat(True)
        ComponentStyle.apply_static(self._btn_open, ComponentKind.BUTTON)
        self._btn_open.clicked.connect(self.open_project_requested.emit)
        layout.addWidget(self._btn_open)

        # 监听状态变化
        ctx.workspace_changed.connect(self.refresh)
        ctx.project_changed.connect(self.refresh)
        ctx.variant_changed.connect(self._on_external_variant_change)
        if theme_view is not None:
            self._apply_theme(theme_view.snapshot())
            theme_view.subscribe(self, self._apply_theme)

    # ── 版本下拉 ──────────────────────────────────────────────

    def _on_variant_selected(self, index: int):
        if index < 0:
            return
        variant_id = self._variant_combo.currentData()
        active = self._ctx.active_variant_id if self._ctx.uses_authoritative_projection else self._ctx.active_variant
        if variant_id and variant_id != active:
            self.variant_switch_requested.emit(variant_id)

    def _on_external_variant_change(self, name: str):
        self._variant_combo.blockSignals(True)
        try:
            for i in range(self._variant_combo.count()):
                if self._variant_combo.itemData(i) == name:
                    self._variant_combo.setCurrentIndex(i)
                    break
        finally:
            self._variant_combo.blockSignals(False)

    # ── 版本管理菜单 ──────────────────────────────────────────

    def _on_variant_menu(self):
        menu = QMenu(self)
        menu.addAction("新建版本...", lambda: self.variant_add_requested.emit())
        menu.addAction("复制当前版本...", lambda: self.variant_copy_requested.emit())

        variants = self._ctx.project_variants
        if len(variants) > 1:
            del_menu = menu.addMenu("删除版本")
            for v in variants:
                variant_id = str(v["id"])
                display_name = str(v["name"])
                del_menu.addAction(
                    display_name,
                    lambda value=variant_id: self.variant_delete_requested.emit(value),
                )

        menu.addSeparator()
        snapshots = menu.addMenu("管理快照")
        snapshots.addAction("创建历史还原点…", self.snapshot_save_requested.emit)
        snapshots.addAction("载入历史还原点…", self.snapshot_load_requested.emit)
        snapshots.addAction("删除历史还原点…", self.snapshot_delete_requested.emit)

        menu.exec(self._variant_menu_btn.mapToGlobal(self._variant_menu_btn.rect().bottomLeft()))

    # ── 重命名项目 ────────────────────────────────────────────

    def _on_rename_project(self):
        if self._ctx.uses_authoritative_projection:
            name = self._ctx.project_name
            if not name:
                return
            new_name, ok = QInputDialog.getText(self, "修改项目名称", "新名称:", text=name)
            if ok and new_name.strip() and new_name.strip() != name:
                self.project_rename_requested.emit(new_name.strip())
            return
        proj = self._ctx.active_project
        if not proj:
            return
        name, ok = QInputDialog.getText(self, "修改项目名称", "新名称:", text=proj.name)
        if ok and name.strip() and name.strip() != proj.name:
            self.project_rename_requested.emit(name.strip())

    def _on_project_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("重命名工程…", self._on_rename_project)
        if self._ctx.uses_authoritative_projection:
            project_id = self._ctx.active_project_id
            name = self._ctx.project_name
            if project_id and name:
                menu.addSeparator()
                menu.addAction(
                    "删除本地工程…",
                    lambda: self.project_delete_requested.emit(str(project_id), str(name)),
                )
        menu.exec(self._rename_btn.mapToGlobal(self._rename_btn.rect().bottomLeft()))

    # ── 刷新 ──────────────────────────────────────────────────

    @property
    def hierarchy_state(self) -> WorkbenchHierarchyViewState:
        return self._hierarchy_state

    @property
    def save_state(self) -> SaveViewState:
        return self._save_presenter.state

    def _target(self) -> SaveTarget | None:
        project_id = getattr(self._ctx, "active_project_id", None)
        project_name = getattr(self._ctx, "project_name", None)
        variant_id = (
            getattr(self._ctx, "active_variant_id", None)
            if self._ctx.uses_authoritative_projection
            else getattr(self._ctx, "active_variant", None)
        )
        variant_name = None
        for value in self._ctx.project_variants:
            if str(value["id"]) == str(variant_id):
                variant_name = str(value["name"])
                break
        if not project_id and not self._ctx.uses_authoritative_projection:
            project_id = getattr(getattr(self._ctx, "active_project", None), "project_dir", None)
            project_id = str(project_id or project_name or "")
        if not all((project_id, project_name, variant_id)):
            return None
        return SaveTarget(str(project_id), str(project_name), str(variant_id), variant_name or str(variant_id))

    def _apply_save_state(self, state: SaveViewState) -> None:
        self._save_status.set_full_text(state.message)
        self._save_status.setToolTip(state.diagnostic or state.message)
        self._save_status.setAccessibleDescription(state.diagnostic or state.message)
        domain_keys = {
            SavePhase.CLEAN: "completed",
            SavePhase.SAVING: "running",
            SavePhase.DIRTY: "paused",
            SavePhase.FAILED: "failed",
        }
        semantic_states = {
            SavePhase.CLEAN: SemanticState.SUCCESS,
            SavePhase.SAVING: SemanticState.INFO,
            SavePhase.DIRTY: SemanticState.WARNING,
            SavePhase.FAILED: SemanticState.ERROR,
        }
        if self._domain is not None:
            palette = self._save_status.palette()
            palette.setColor(
                QPalette.ColorRole.WindowText,
                self._domain.task(domain_keys[state.phase]).foreground.color(),
            )
            self._save_status.setPalette(palette)
        ComponentStyle.apply_state(self._save_status, semantic_states[state.phase])
        ComponentStyle.apply_state(self._save_btn, semantic_states[state.phase])
        self._save_btn.setText("重试保存" if state.phase is SavePhase.FAILED else "保存")
        self._save_btn.setEnabled(state.target is not None and state.phase is not SavePhase.SAVING)

    def refresh(self):
        if self._ctx.uses_authoritative_projection:
            project_name = self._ctx.project_name
            variants = self._ctx.project_variants
            variant = self._ctx.active_variant_id
        else:
            proj = self._ctx.active_project
            project_name = None if proj is None else proj.name
            variants = self._ctx.project_variants
            variant = self._ctx.active_variant

        variant_name = None
        for value in variants:
            if str(value["id"]) == str(variant):
                variant_name = str(value["name"])
                break
        self._hierarchy_state = self._workflow_presenter.hierarchy(
            project_id=getattr(self._ctx, "active_project_id", None) or project_name,
            project_name=project_name,
            variant_id=variant,
            variant_name=variant_name,
            sources=getattr(self._ctx, "project_sources", ()),
        )

        self._variant_combo.blockSignals(True)
        try:
            self._variant_combo.clear()
            if project_name and variants:
                for v in variants:
                    self._variant_combo.addItem(str(v["name"]), str(v["id"]))
                if variant:
                    for i in range(self._variant_combo.count()):
                        if self._variant_combo.itemData(i) == variant:
                            self._variant_combo.setCurrentIndex(i)
                            break
        finally:
            self._variant_combo.blockSignals(False)

        if project_name:
            self._project_label.set_full_text(project_name)
            self._project_label.setToolTip(project_name)
            self._rename_btn.setVisible(True)
            self._variant_combo.setVisible(True)
            self._variant_menu_btn.setVisible(True)
            self._save_btn.setVisible(True)
            self._btn_new.setVisible(False)
            self._btn_open.setVisible(False)
            target = self._target()
            if target is not None:
                self._apply_save_state(self._save_presenter.show_target(target, dirty=self._ctx.dirty))
        else:
            empty_label = "无项目 - 请新建或打开项目"
            self._project_label.set_full_text(empty_label)
            self._project_label.setToolTip(empty_label)
            self._rename_btn.setVisible(False)
            self._variant_combo.setVisible(False)
            self._variant_menu_btn.setVisible(False)
            self._save_btn.setVisible(False)
            self._btn_new.setVisible(True)
            self._btn_open.setVisible(True)
            self._apply_save_state(self._save_presenter.clear())

    # ── 保存反馈 ──────────────────────────────────────────────

    def flash_saved(self):
        target = self._target()
        if target is not None:
            self._save_presenter.begin(target)
            self._apply_save_state(self._save_presenter.succeed(target, saved_at=datetime.now().astimezone()))
        ComponentStyle.apply_state(self._save_btn, SemanticState.SUCCESS)
        self._save_btn.setText("已保存")
        self._save_btn.setAccessibleDescription("保存成功")
        QTimer.singleShot(1500, self._reset_save_style)

    def _reset_save_style(self):
        self._apply_save_state(self._save_presenter.state)

    def set_save_dirty(self, dirty: bool):
        target = self._target()
        if target is None:
            self._apply_save_state(self._save_presenter.clear())
            return
        if dirty:
            state = self._save_presenter.mark_dirty(target)
        else:
            state = self._save_presenter.show_target(target, dirty=False)
        self._apply_save_state(state)

    def set_save_saving(self) -> None:
        target = self._target()
        if target is not None:
            self._apply_save_state(self._save_presenter.begin(target))

    def set_save_failed(self, diagnostic: str) -> None:
        target = self._target()
        if target is not None:
            if self._save_presenter.state.phase is not SavePhase.SAVING:
                self._save_presenter.begin(target)
            self._apply_save_state(self._save_presenter.fail(target, diagnostic))

    def _apply_theme(self, snapshot) -> None:
        try:
            domain = self._domain_factory(snapshot)
        except Exception:  # noqa: BLE001 - preserve the last-good visual state
            return
        self._domain = domain
        self._refresh_variant_icon()
        project_palette = self._project_label.palette()
        project_palette.setColor(QPalette.ColorRole.WindowText, domain.report("info").foreground.color())
        self._project_label.setPalette(project_palette)
        self._apply_save_state(self._save_presenter.state)

    def _refresh_variant_icon(self) -> None:
        self._variant_menu_btn.setIcon(tabler_icon(self._variant_menu_btn, "plus", 16))
