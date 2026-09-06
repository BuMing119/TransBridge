"""Read-only Qt search surface for the persisted-history projection."""

from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from transbridge.application.history_search import (
    HistoryEntryKind,
    HistoryQuery,
    HistorySearchHit,
    HistorySearchScope,
    HistorySearchScopeKind,
    HistorySourceType,
)
from transbridge.application.tasks import JobEventType, JobState, OwnerRef, TaskEventFilter
from transbridge.ui.windows_taskbar import clear_window_app_user_model_id, set_window_app_user_model_id


class _QuerySignals(QObject):
    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(int, object)


class _QueryRunnable(QRunnable):
    def __init__(self, generation, call, signals, cancelled) -> None:
        super().__init__()
        self._generation = generation
        self._call = call
        self._signals = signals
        self._cancelled = cancelled

    def run(self) -> None:
        if self._cancelled.is_set():
            return
        try:
            result = self._call()
        except Exception as exc:  # noqa: BLE001 - query adapter boundary
            if not self._cancelled.is_set():
                self._signals.failed.emit(self._generation, exc)
            return
        if not self._cancelled.is_set():
            self._signals.succeeded.emit(self._generation, result)


class HistorySearchWindow(QDialog):
    task_finished = pyqtSignal(object)

    def __init__(
        self,
        index,
        tasks,
        owner: OwnerRef,
        parent: QWidget | None = None,
        *,
        taskbar_app_user_model_id: str | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._index = index
        self._tasks = tasks
        self._owner = owner
        self._pool = QThreadPool.globalInstance()
        self._signals = _QuerySignals()
        self._signals.succeeded.connect(self._accept_query, Qt.ConnectionType.QueuedConnection)
        self._signals.failed.connect(self._reject_query, Qt.ConnectionType.QueuedConnection)
        self._cancelled = threading.Event()
        self._generation = 0
        self._active_ref = None
        self._diagnostics = ()
        self._taskbar_app_user_model_id = taskbar_app_user_model_id
        self._taskbar_identity_applied = False
        self._subscription = tasks.runtime.subscribe(
            self._on_task_event,
            event_filter=TaskEventFilter(
                owner_id=owner.owner_id,
                event_types=frozenset({JobEventType.FINISHED}),
            ),
        )
        self.task_finished.connect(self._handle_task_finished, Qt.ConnectionType.QueuedConnection)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._start_query)
        self.setWindowTitle("历史翻译与术语搜索")
        self.setAccessibleName("历史翻译与术语搜索")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1180, 760)
        self.setMinimumSize(820, 560)
        self._build_ui()
        self._render_index_status()
        if self._index.status().ready:
            self._reload_scopes()
            self._start_query()
        else:
            self.refresh_index()
        self._prepare_taskbar_identity()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("输入原文或译文；留空显示全部")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._schedule_query)
        controls.addWidget(self.search_edit, 1)
        controls.addWidget(QLabel("范围", self))
        self.scope_combo = QComboBox(self)
        self.scope_combo.setMinimumContentsLength(18)
        self.scope_combo.addItem("全部来源", None)
        self.scope_combo.currentIndexChanged.connect(self._schedule_query)
        controls.addWidget(self.scope_combo)
        controls.addWidget(QLabel("类型", self))
        self.kind_combo = QComboBox(self)
        self.kind_combo.addItem("全部", None)
        self.kind_combo.addItem("完整译文", HistoryEntryKind.TRANSLATION.value)
        self.kind_combo.addItem("术语", HistoryEntryKind.TERM.value)
        self.kind_combo.currentIndexChanged.connect(self._schedule_query)
        controls.addWidget(self.kind_combo)
        self.refresh_button = QPushButton("刷新索引", self)
        self.refresh_button.clicked.connect(self.refresh_index)
        controls.addWidget(self.refresh_button)
        root.addLayout(controls)

        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.results = QTableWidget(0, 6, splitter)
        self.results.setHorizontalHeaderLabels(("来源 ID", "类型", "原文", "译文", "范围", "状态"))
        self.results.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results.setAlternatingRowColors(True)
        self.results.verticalHeader().setVisible(False)
        header = self.results.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.results.itemSelectionChanged.connect(self._show_sources)
        self.results.itemDoubleClicked.connect(lambda _item: self.copy_translation())
        splitter.addWidget(self.results)

        detail = QWidget(splitter)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 6, 0, 0)
        detail_layout.addWidget(QLabel("来源详情（合并结果仍保留每个真实来源）", detail))
        self.sources = QListWidget(detail)
        detail_layout.addWidget(self.sources)
        splitter.addWidget(detail)
        splitter.setSizes((520, 170))
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.status_label = QLabel(self)
        self.status_label.setWordWrap(True)
        footer.addWidget(self.status_label, 1)
        self.diagnostics_button = QPushButton("查看诊断", self)
        self.diagnostics_button.setEnabled(False)
        self.diagnostics_button.clicked.connect(self._show_diagnostics)
        footer.addWidget(self.diagnostics_button)
        self.copy_button = QPushButton("复制译文", self)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_translation)
        footer.addWidget(self.copy_button)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        root.addLayout(footer)

    def refresh_index(self) -> None:
        if self._active_ref is not None:
            try:
                self._tasks.runtime.cancel(self._active_ref, self._owner)
            except Exception:  # noqa: BLE001 - terminal completion can race with this click
                snapshot = self._tasks.runtime.get(self._active_ref, self._owner)
                self._finish_snapshot(snapshot)
            else:
                self.refresh_button.setEnabled(False)
                self.status_label.setText("正在取消索引刷新…")
            return
        try:
            deferred = self._tasks.refresh(self._owner)
        except Exception as exc:  # noqa: BLE001 - keep the read-only window usable
            self.status_label.setText(f"无法启动索引刷新：{exc}")
            return
        self._active_ref = deferred.ref
        self.refresh_button.setText("取消刷新")
        self.status_label.setText("正在后台读取已保存的 Project/Variant、.tbdict 和生效术语…")
        snapshot = self._tasks.runtime.get(deferred.ref, self._owner)
        if snapshot.is_terminal:
            self._finish_snapshot(snapshot)

    def copy_translation(self) -> None:
        hit = self._selected_hit()
        application = QApplication.instance()
        if hit is None or application is None:
            return
        application.clipboard().setText(hit.translation)
        self.status_label.setText("译文已复制到剪贴板。")

    def _schedule_query(self) -> None:
        self._generation += 1
        self._debounce.start()

    def _start_query(self) -> None:
        generation = self._generation
        kind_value = self.kind_combo.currentData()
        kind = None if kind_value is None else HistoryEntryKind(kind_value)
        scope = self.scope_combo.currentData()
        request = HistoryQuery(
            self.search_edit.text(),
            kind=kind,
            scope=scope if isinstance(scope, HistorySearchScope) else None,
        )
        self.status_label.setText("正在搜索…")
        self._pool.start(_QueryRunnable(generation, lambda: self._index.query(request), self._signals, self._cancelled))

    def _accept_query(self, generation: int, page) -> None:
        if generation != self._generation:
            return
        self.results.setRowCount(0)
        for hit in page.items:
            row = self.results.rowCount()
            self.results.insertRow(row)
            values = (
                _source_ids(hit),
                "术语" if hit.kind is HistoryEntryKind.TERM else "完整译文",
                hit.original,
                hit.translation,
                hit.scope_key or _locale_label(hit),
                _status_label(hit),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, hit)
                self.results.setItem(row, column, item)
        notes = []
        if len(page.items) < page.total:
            notes.append(f"当前显示前 {len(page.items)} 条")
        if page.truncated:
            notes.append("候选集已达查询上限")
        suffix = f"（{'；'.join(notes)}）" if notes else ""
        self.status_label.setText(f"找到 {page.total} 条结果{suffix}。")
        if page.items:
            self.results.selectRow(0)
        else:
            self.sources.clear()
            self.copy_button.setEnabled(False)

    def _reject_query(self, generation: int, error: object) -> None:
        if generation == self._generation:
            self.status_label.setText(f"搜索失败：{error}")

    def _show_sources(self) -> None:
        hit = self._selected_hit()
        self.sources.clear()
        self.copy_button.setEnabled(hit is not None)
        if hit is None:
            return
        for source in hit.sources:
            details = "；".join(f"{key}={value}" for key, value in source.details)
            identity = source.project_id or source.dictionary_id or source.plugin_id or source.source_id
            text = f"{identity}｜{source.label}"
            self.sources.addItem(text if not details else f"{text}｜{details}")

    def _selected_hit(self) -> HistorySearchHit | None:
        row = self.results.currentRow()
        if row < 0:
            return None
        item = self.results.item(row, 0)
        value = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, HistorySearchHit) else None

    def _on_task_event(self, event) -> None:
        if event.snapshot.specification.job_type == "history-search.refresh":
            self.task_finished.emit(event)

    def _handle_task_finished(self, event) -> None:
        snapshot = event.snapshot
        if self._active_ref is not None and snapshot.ref == self._active_ref:
            self._finish_snapshot(snapshot)
        elif snapshot.state is JobState.COMPLETED:
            self._reload_scopes()
            self._generation += 1
            self._start_query()

    def _finish_snapshot(self, snapshot) -> None:
        if self._active_ref is None or snapshot.ref != self._active_ref:
            return
        self._active_ref = None
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新索引")
        if snapshot.state is JobState.COMPLETED:
            status = self._index.status()
            self._set_diagnostics(status.diagnostics)
            self._reload_scopes()
            diagnostic = f"；{len(status.diagnostics)} 个来源诊断" if status.diagnostics else ""
            self.status_label.setText(f"索引已刷新，共 {status.record_count} 条来源记录{diagnostic}。")
            self._generation += 1
            self._start_query()
        elif snapshot.state is JobState.CANCELLED:
            self.status_label.setText("索引刷新已取消；仍保留上一次完整索引。")
        else:
            self.status_label.setText("索引刷新失败；仍保留上一次完整索引。")

    def _render_index_status(self) -> None:
        status = self._index.status()
        self._set_diagnostics(status.diagnostics)
        if not status.ready:
            self.status_label.setText("尚未建立搜索索引。")
        else:
            self.status_label.setText(f"索引包含 {status.record_count} 条来源记录；关键词留空时显示全部。")

    def _reload_scopes(self) -> None:
        selected = self.scope_combo.currentData()
        selected_key = None
        if isinstance(selected, HistorySearchScope):
            selected_key = (selected.kind, selected.scope_id)
        self.scope_combo.blockSignals(True)
        try:
            self.scope_combo.clear()
            self.scope_combo.addItem("全部来源", None)
            selected_index = 0
            for scope in self._index.scopes():
                prefix = "项目" if scope.kind is HistorySearchScopeKind.PROJECT else "词典"
                self.scope_combo.addItem(f"{prefix}｜{scope.label}", scope)
                if selected_key == (scope.kind, scope.scope_id):
                    selected_index = self.scope_combo.count() - 1
            self.scope_combo.setCurrentIndex(selected_index)
        finally:
            self.scope_combo.blockSignals(False)

    def _set_diagnostics(self, diagnostics) -> None:
        self._diagnostics = tuple(diagnostics)
        self.diagnostics_button.setEnabled(bool(self._diagnostics))

    def _show_diagnostics(self) -> None:
        if not self._diagnostics:
            return
        visible = self._diagnostics[:100]
        lines = [f"• {item.source or '本地数据'}：{item.message}（{item.code}）" for item in visible]
        if len(self._diagnostics) > len(visible):
            lines.append(f"…另有 {len(self._diagnostics) - len(visible)} 条诊断未展开。")
        QMessageBox.information(self, "索引刷新诊断", "\n".join(lines))

    def _prepare_taskbar_identity(self) -> None:
        if self._taskbar_identity_applied or not self._taskbar_app_user_model_id:
            return
        self._taskbar_identity_applied = set_window_app_user_model_id(self, self._taskbar_app_user_model_id)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._prepare_taskbar_identity()
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        self._cancelled.set()
        self._subscription.close()
        if self._taskbar_identity_applied:
            clear_window_app_user_model_id(self)
            self._taskbar_identity_applied = False
        super().closeEvent(event)


def _source_ids(hit: HistorySearchHit) -> str:
    values = []
    for source in hit.sources:
        if source.source_type is HistorySourceType.PROJECT_VARIANT:
            value = source.plugin_id or source.project_id or source.source_id
        elif source.source_type is HistorySourceType.TERMINOLOGY:
            value = source.plugin_id or source.project_id or source.source_id
        else:
            value = source.dictionary_id or source.plugin_id or source.source_id
        if value not in values:
            values.append(value)
    return ", ".join(values)


def _locale_label(hit: HistorySearchHit) -> str:
    if hit.source_locale or hit.target_locale:
        return f"{hit.source_locale or '?'} → {hit.target_locale or '?'}"
    return "—"


def _status_label(hit: HistorySearchHit) -> str:
    values = []
    if hit.has_alternatives:
        values.append("存在不同译法")
    if len(hit.sources) > 1:
        values.append(f"{len(hit.sources)} 个来源")
    if hit.status:
        values.append(hit.status)
    return "；".join(values) or "—"


__all__ = ["HistorySearchWindow"]
