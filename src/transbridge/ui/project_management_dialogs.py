# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Desktop management views for the active project and its history."""

from PyQt6 import sip
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from transbridge.ui.foundation.components import ComponentKind, ComponentStyle, WrappingLabel
from transbridge.ui.shell.action_catalog import IntentId


class _ProjectDialog(QDialog):
    def __init__(self, parent, context, title: str, *, fixed_variant: bool = False) -> None:
        super().__init__(parent)
        self.context = context
        self.project_id = context.active_project_id
        self.scope = context.active_version_identity if fixed_variant else None
        self._closed = False
        self._detached = False
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(540, 360)
        self._layout = QVBoxLayout(self)
        self._target = WrappingLabel(parent=self)
        self._target.setAccessibleName("当前操作对象")
        self._layout.addWidget(self._target)
        self._status = WrappingLabel(parent=self)
        self._status.setAccessibleName("操作状态")
        self._layout.addWidget(self._status)
        self._context_timer = QTimer(self)
        self._context_timer.setSingleShot(True)
        self._context_timer.timeout.connect(self.refresh)
        context.project_changed.connect(self._queue_refresh)
        context.variant_changed.connect(self._queue_refresh)
        context.workspace_changed.connect(self._queue_refresh)
        self.finished.connect(self._mark_closed)
        self.refresh_target_label()

    @property
    def disposed(self) -> bool:
        return self._closed or sip.isdeleted(self)

    def _mark_closed(self) -> None:
        self._closed = True

    def _queue_refresh(self, *_args) -> None:
        self._detached = self._detached or not self.targets_current_scope()
        self._context_timer.start(0)

    def targets_current_scope(self) -> bool:
        return not self._detached and self._is_bound_scope()

    def _is_bound_scope(self) -> bool:
        return self.context.active_project_id == self.project_id and (
            self.scope is None or self.context.active_version_identity == self.scope
        )

    def unavailable_reason(self) -> str:
        if not self.targets_current_scope():
            return "当前工程或翻译版本已切换，请重新打开此窗口。"
        if self.context.project_opening:
            return "正在加载工作台，请稍候。"
        if self.context.project_view_failed:
            return "工作台未能加载，请重新打开工程。"
        return ""

    def refresh_target_label(self) -> None:
        project = self.context.project_name or "未命名工程"
        if self.scope is None:
            self._target.setText(f"工程 · {project}")
        else:
            variant = next(
                (str(item["name"]) for item in self.context.project_variants if item["id"] == self.scope[1]),
                self.scope[1],
            )
            self._target.setText(f"工程 · {project}\n翻译版本 · {variant}")

    def set_status(self, message: str) -> None:
        self._status.setText(message)
        self._status.setVisible(bool(message))

    def add_close_button(self) -> None:
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        close.rejected.connect(self.reject)
        self._layout.addWidget(close)

    def button(self, row: QHBoxLayout, text: str, callback) -> QPushButton:
        button = QPushButton(text, self)
        ComponentStyle.apply_static(button, ComponentKind.BUTTON)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button


class VariantManagementDialog(_ProjectDialog):
    def __init__(self, parent, context, *, dispatch, availability, switch) -> None:
        super().__init__(parent, context, "管理翻译版本")
        self._dispatch = dispatch
        self._availability = availability
        self._switch = switch
        self._versions = QListWidget(self)
        self._versions.setAccessibleName("翻译版本列表")
        self._versions.currentItemChanged.connect(self._update_actions)
        self._versions.itemActivated.connect(lambda _item: self._switch_selected())
        self._layout.addWidget(self._versions, 1)
        row = QHBoxLayout()
        self._create = self.button(row, "新建…", lambda: self._run(IntentId.PROJECT_VARIANT_CREATE))
        self._copy = self.button(row, "复制当前版本…", lambda: self._run(IntentId.PROJECT_VARIANT_COPY))
        self._activate = self.button(row, "切换到所选版本", self._switch_selected)
        row.addStretch()
        self._layout.addLayout(row)
        cache = QHBoxLayout()
        self._clear = self.button(
            cache, "清除当前版本定位缓存", lambda: self._run(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR)
        )
        cache.addStretch()
        self._layout.addLayout(cache)
        self.add_close_button()
        self.refresh()

    def refresh(self) -> None:
        if self.disposed:
            return
        reason = self.unavailable_reason()
        selected = self._selected_id()
        current = self.context.active_variant_id
        self._versions.blockSignals(True)
        try:
            self._versions.clear()
            if not reason:
                self.refresh_target_label()
                for value in self.context.project_variants:
                    identity = str(value["id"])
                    name = str(value["name"])
                    text = f"{name}（当前）" if identity == current else name
                    item = QListWidgetItem(text, self._versions)
                    item.setData(Qt.ItemDataRole.UserRole, identity)
                    if identity == (selected or current):
                        self._versions.setCurrentItem(item)
        finally:
            self._versions.blockSignals(False)
        self.set_status(reason or ("当前工程没有翻译版本。" if not self._versions.count() else ""))
        self._update_actions()

    def _selected_id(self) -> str | None:
        item = self._versions.currentItem()
        return None if item is None else item.data(Qt.ItemDataRole.UserRole)

    def _update_actions(self) -> None:
        ready = not self.unavailable_reason()
        current = self.context.active_variant_id
        selected = self._selected_id()
        self._create.setEnabled(ready)
        self._copy.setEnabled(ready and bool(current))
        self._activate.setEnabled(ready and bool(selected) and selected != current)
        cache = self._availability(IntentId.SETTINGS_ALIGNMENT_CACHE_CLEAR)
        self._clear.setEnabled(ready and cache.enabled)
        self._clear.setToolTip(cache.reason or "清除当前翻译版本的定位判断")

    def _run(self, intent: IntentId) -> None:
        self.refresh()
        if not self.unavailable_reason():
            self._dispatch(intent)

    def _switch_selected(self) -> None:
        target = self._selected_id()
        if not self.unavailable_reason() and target and target != self.context.active_variant_id:
            self._switch(target)


class SnapshotHistoryDialog(_ProjectDialog):
    def __init__(self, parent, context, *, transfer) -> None:
        super().__init__(parent, context, "历史还原", fixed_variant=True)
        self._transfer = transfer
        self._busy = False
        self._selection_ready = False
        self._snapshots = QListWidget(self)
        self._snapshots.setAccessibleName("历史还原点列表")
        self._snapshots.currentItemChanged.connect(self._update_actions)
        self._layout.addWidget(self._snapshots, 1)
        row = QHBoxLayout()
        self._create = self.button(row, "创建还原点…", self._create_snapshot)
        self._restore = self.button(row, "恢复所选还原点…", lambda: self._apply(self._transfer.restore_snapshot))
        self._delete = self.button(row, "删除所选还原点…", lambda: self._apply(self._transfer.remove_snapshot))
        self._reload = self.button(row, "刷新", self._retry)
        row.addStretch()
        self._layout.addLayout(row)
        self.add_close_button()
        self.reload()

    def refresh(self) -> None:
        if self.disposed:
            return
        reason = self.unavailable_reason()
        if reason:
            self._selection_ready = False
            self._snapshots.clear()
            returned = self._detached and self._is_bound_scope()
            self.set_status("已返回原翻译版本，请刷新还原点列表。" if returned else reason)
        else:
            self.refresh_target_label()
        self._update_actions()

    def reload(self) -> None:
        self.refresh()
        if self.disposed or self._busy or self.scope is None or self.unavailable_reason():
            return
        self._busy = True
        self._selection_ready = False
        self._snapshots.clear()
        self.set_status("正在读取历史还原点…")
        self._update_actions()
        if not self._transfer.list_snapshots(
            self.scope, on_result=self._show_snapshots, on_error=self._load_failed, on_finished=self._read_finished
        ):
            self._load_failed("另一项后台操作仍在进行。请稍后刷新。")
            self._read_finished()

    def _retry(self) -> None:
        if self._is_bound_scope():
            self._detached = False
            self.reload()

    def _show_snapshots(self, snapshots) -> None:
        if self.disposed or self.unavailable_reason():
            return
        for snapshot in snapshots:
            item = QListWidgetItem(f"{snapshot.name}（修订 {snapshot.revision}）", self._snapshots)
            item.setData(Qt.ItemDataRole.UserRole, snapshot)
            item.setToolTip(snapshot.identity)
        self._selection_ready = True
        self.set_status("" if snapshots else "当前版本尚无历史还原点。")

    def _load_failed(self, error: str) -> None:
        if not self.disposed and not self.unavailable_reason():
            self.set_status(f"无法读取历史还原点：{error}")

    def _read_finished(self) -> None:
        self._busy = False
        self.refresh()

    def _update_actions(self) -> None:
        ready = self.scope is not None and not self._busy and not self.unavailable_reason()
        selected = ready and self._selection_ready and self._snapshots.currentItem() is not None
        self._create.setEnabled(ready)
        self._reload.setEnabled(ready or (not self._busy and self._detached and self._is_bound_scope()))
        self._restore.setEnabled(selected)
        self._delete.setEnabled(selected)

    def _run_operation(self, operation, *args, **kwargs) -> None:
        if self._busy or self.scope is None or self.unavailable_reason():
            self.refresh()
            return
        self._busy = True
        self._update_actions()
        if not operation(*args, **kwargs, on_finished=self._operation_finished):
            self._read_finished()

    def _operation_finished(self) -> None:
        self._busy = False
        self.reload()

    def _create_snapshot(self) -> None:
        self._run_operation(self._transfer.save_snapshot, scope=self.scope)

    def _apply(self, operation) -> None:
        item = self._snapshots.currentItem()
        if self._selection_ready and item is not None:
            self._run_operation(operation, item.data(Qt.ItemDataRole.UserRole), self.scope)


class ProjectPropertiesDialog(_ProjectDialog):
    def __init__(self, parent, context, *, dispatch, availability) -> None:
        super().__init__(parent, context, "工程属性")
        self._dispatch = dispatch
        self._availability = availability
        self.resize(460, 185)
        row = QHBoxLayout()
        self._rename = self.button(row, "重命名工程…", lambda: self._run(IntentId.PROJECT_RENAME))
        self._encoding = self.button(row, "文本编码…", lambda: self._run(IntentId.PROJECT_TEXT_ENCODING))
        row.addStretch()
        self._layout.addLayout(row)
        self._layout.addStretch()
        self.add_close_button()
        self.refresh()

    def refresh(self) -> None:
        if self.disposed:
            return
        reason = self.unavailable_reason()
        if not reason:
            self.refresh_target_label()
        encoding = self._availability(IntentId.PROJECT_TEXT_ENCODING)
        self._rename.setEnabled(not reason)
        self._encoding.setEnabled(not reason and encoding.enabled)
        self._encoding.setToolTip(encoding.reason or "修改工程语言及文本编码设置")
        self.set_status(reason or encoding.reason or "")

    def _run(self, intent: IntentId) -> None:
        self.refresh()
        if not self.unavailable_reason():
            self._dispatch(intent)
