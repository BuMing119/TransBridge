"""Dialogs for guiding and managing local embedding models.

The manager intentionally depends on a small duck-typed store instead of a
concrete infrastructure implementation.  This keeps filesystem and download
policy outside the Qt layer and makes the UI straightforward to exercise with
an in-memory store.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LocalEmbeddingGuideDialog(QDialog):
    """Explain an unavailable local semantic-search service.

    ``decision`` is always one of ``"disable"`` and ``"configure"``.  The
    conservative default is ``"disable"`` so closing the window, pressing
    Escape, or calling :meth:`reject` can never leave an enabled-but-unusable
    configuration behind.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._decision = "disable"
        self.setObjectName("localEmbeddingGuideDialog")
        self.setWindowTitle("本地语义检索暂不可用")
        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)

        title = QLabel("需要先安装本地向量模型", self)
        title.setObjectName("guideTitle")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        explanation = QLabel(
            "当前没有可用的本地向量模型，因此语义检索服务暂时不可用。\n\n"
            "你仍然可以正常使用翻译和字面术语匹配；也可以前往模型配置，选择并下载一个预设模型。",
            self,
        )
        explanation.setObjectName("guideExplanation")
        explanation.setWordWrap(True)
        explanation.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(explanation)

        note = QLabel("关闭后，语义检索将被设为关闭状态。", self)
        note.setObjectName("guideNote")
        note.setForegroundRole(QPalette.ColorRole.PlaceholderText)
        note.setProperty("tbSecondary", True)
        layout.addWidget(note)

        actions = QHBoxLayout()
        actions.addStretch()
        disable_button = QPushButton("关闭语义检索", self)
        disable_button.setObjectName("disableButton")
        disable_button.clicked.connect(self.reject)
        actions.addWidget(disable_button)
        configure_button = QPushButton("前往模型配置", self)
        configure_button.setObjectName("configureButton")
        configure_button.setDefault(True)
        configure_button.clicked.connect(self._choose_configure)
        actions.addWidget(configure_button)
        layout.addLayout(actions)

    @property
    def decision(self) -> str:
        """Return ``"configure"`` only after an explicit configure action."""

        return self._decision

    def _choose_configure(self) -> None:
        self._decision = "configure"
        super().accept()

    def reject(self) -> None:
        self._decision = "disable"
        super().reject()


class _DownloadWorker(QThread):
    """Run one store download without blocking the GUI thread."""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int, str)

    def __init__(self, store: Any, model_id: str) -> None:
        super().__init__()
        self._store = store
        self._model_id = model_id
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancelled.is_set()

    def _report_progress(self, current: int, total: int | None = None, message: str = "") -> None:
        """Accept the store's percentage callback and legacy current/total callbacks."""

        if total is None:
            self.progress_changed.emit(int(current), 100, str(message))
            return
        self.progress_changed.emit(int(current), int(total), str(message))

    def run(self) -> None:
        try:
            path = self._store.download(
                self._model_id,
                progress=self._report_progress,
                cancelled=self._cancelled.is_set,
            )
        except Exception as exc:  # UI boundary: display the store's actionable error.
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(Path(path))


class EmbeddingModelManagerDialog(QDialog):
    """Browse, install, select, and remove preset local embedding models.

    The store must provide ``list_models()``, ``download()``, ``remove()``, and
    ``installed_path()``.  No concrete store class is imported here.
    """

    def __init__(
        self,
        store: Any,
        current_model_path: str | Path | None = None,
        parent: QWidget | None = None,
        on_before_remove_current: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._selected_model_path = Path(current_model_path) if current_model_path else None
        self._selected_model_id: str | None = None
        self._states: list[Any] = []
        self._worker: _DownloadWorker | None = None
        self._on_before_remove_current = on_before_remove_current
        self._restore_on_application_activate = False
        self._pending_download_completion: tuple[str, Path] | None = None
        self._completion_message: QMessageBox | None = None
        self._application = QApplication.instance()

        self.setObjectName("embeddingModelManagerDialog")
        self.setWindowTitle("本地向量模型")
        self.setModal(True)
        self.resize(680, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        title = QLabel("选择本地向量模型", self)
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)
        description = QLabel(
            "模型仅在需要时下载到本机。下载完成后会自动设为当前模型，你也可以在已安装模型间切换。",
            self,
        )
        description.setWordWrap(True)
        root.addWidget(description)

        self._model_list = QListWidget(self)
        self._model_list.setObjectName("modelList")
        self._model_list.setAlternatingRowColors(True)
        self._model_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._model_list.currentRowChanged.connect(self._update_actions)
        root.addWidget(self._model_list, 1)

        status_frame = QFrame(self)
        status_frame.setFrameShape(QFrame.Shape.StyledPanel)
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 8, 10, 8)
        self._status = QLabel("", status_frame)
        self._status.setObjectName("statusLabel")
        self._status.setWordWrap(True)
        status_layout.addWidget(self._status)
        self._progress = QProgressBar(status_frame)
        self._progress.setObjectName("progressBar")
        self._progress.setRange(0, 100)
        self._progress.hide()
        status_layout.addWidget(self._progress)
        root.addWidget(status_frame)

        actions = QHBoxLayout()
        self._download_button = QPushButton("下载", self)
        self._download_button.setObjectName("downloadButton")
        self._download_button.clicked.connect(self._download_or_cancel)
        actions.addWidget(self._download_button)
        self._use_button = QPushButton("设为当前", self)
        self._use_button.setObjectName("useButton")
        self._use_button.clicked.connect(self._use_selected)
        actions.addWidget(self._use_button)
        self._remove_button = QPushButton("删除", self)
        self._remove_button.setObjectName("removeButton")
        self._remove_button.clicked.connect(self._remove_selected)
        actions.addWidget(self._remove_button)
        actions.addStretch()
        self._close_button = QPushButton("关闭", self)
        self._close_button.setObjectName("closeButton")
        self._close_button.clicked.connect(self.accept)
        actions.addWidget(self._close_button)
        root.addLayout(actions)

        self.refresh_models()
        if self._application is not None:
            self._application.applicationStateChanged.connect(self._on_application_state_changed)
            self.finished.connect(self._disconnect_application_activation)

    @property
    def selected_model_path(self) -> Path | None:
        """Path chosen for use, or ``None`` when no local model is selected."""

        return self._selected_model_path

    @property
    def selected_model_id(self) -> str | None:
        """Stable catalog identity chosen for use, or ``None``."""

        return self._selected_model_id

    def refresh_models(self, *, preferred_id: str | None = None, status_message: str | None = None) -> None:
        """Reload model states from the store while preserving selection."""

        selected_id = preferred_id or self._current_model_id()
        load_error = ""
        try:
            self._states = list(self._store.list_models())
        except Exception as exc:
            self._states = []
            load_error = f"无法读取模型列表：{exc}"

        self._model_list.clear()
        selected_row = -1
        for row, state in enumerate(self._states):
            preset = state.preset
            tags = []
            if bool(getattr(preset, "recommended", False)):
                tags.append("推荐")
            tags.append("已安装" if bool(state.installed) else "未安装")
            summary = " · ".join(tags)
            size = float(preset.download_size_mb)
            detail = f"{preset.description}\n{preset.dimension} 维 · {size:g} MB · {summary}"
            item = QListWidgetItem(f"{preset.title}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, str(preset.id))
            item.setToolTip(detail)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{preset.title}，{summary}")
            self._model_list.addItem(item)
            if str(preset.id) == selected_id:
                selected_row = row
            elif self._path_matches_selected(state) and selected_row < 0:
                selected_row = row
                self._selected_model_id = str(preset.id)

        if self._states:
            self._model_list.setCurrentRow(selected_row if selected_row >= 0 else 0)
            installed_count = sum(bool(state.installed) for state in self._states)
            self._status.setText(status_message or f"{installed_count} 个已安装，共 {len(self._states)} 个预设模型。")
        else:
            self._status.setText(load_error or status_message or "当前没有可用的预设模型。")
        self._update_actions()

    def _path_matches_selected(self, state: Any) -> bool:
        if self._selected_model_path is None or not bool(state.installed):
            return False
        path = getattr(state, "path", None)
        if path is None:
            try:
                path = self._store.installed_path(str(state.preset.id))
            except Exception:
                return False
        return path is not None and Path(path) == self._selected_model_path

    def _current_state(self) -> Any | None:
        row = self._model_list.currentRow()
        if 0 <= row < len(self._states):
            return self._states[row]
        return None

    def _current_model_id(self) -> str | None:
        item = self._model_list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value is not None else None

    def _update_actions(self, *_args: object) -> None:
        state = self._current_state()
        busy = self._worker is not None
        installed = state is not None and bool(state.installed)
        is_current = installed and self._path_matches_selected(state)
        self._model_list.setEnabled(not busy)
        self._download_button.setEnabled(state is not None and (busy or not installed))
        self._download_button.setText("取消下载" if busy else "下载")
        self._use_button.setEnabled(installed and not busy and not is_current)
        self._use_button.setText("当前使用" if is_current else "设为当前")
        self._remove_button.setEnabled(installed and not busy)
        self._close_button.setEnabled(not busy)

    def _download_or_cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._download_button.setEnabled(False)
            self._status.setText("正在取消下载…")
            return
        state = self._current_state()
        if state is None or bool(state.installed):
            return
        model_id = str(state.preset.id)
        self._progress.setRange(0, 0)
        self._progress.setValue(0)
        self._progress.show()
        self._status.setText(f"正在下载 {state.preset.title}…")

        worker = _DownloadWorker(self._store, model_id)
        worker.progress_changed.connect(self._on_download_progress)
        worker.succeeded.connect(lambda path: self._on_download_succeeded(model_id, Path(path)))
        worker.failed.connect(self._on_download_failed)
        worker.finished.connect(lambda: self._on_download_finished(worker, model_id))
        self._worker = worker
        self._update_actions()
        worker.start()

    def _on_download_progress(self, current: int, total: int, message: str) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(max(0, min(current, total)))
        elif total == 0:
            self._progress.setRange(0, 0)
        # A negative total carries byte/status feedback without replacing the
        # current indeterminate or reliable file-count progress bar.
        if message:
            self._status.setText(message)

    def _on_download_succeeded(self, model_id: str, path: Path) -> None:
        self._selected_model_id = model_id
        self._selected_model_path = path
        title = next(
            (str(state.preset.title) for state in self._states if str(state.preset.id) == model_id),
            path.name,
        )
        self._pending_download_completion = (title, path)
        self._status.setText(f"下载完成，已将 {title} 设为当前模型。")

    def _on_download_failed(self, error: str) -> None:
        if self._worker is not None and self._worker.cancel_requested:
            self._status.setText("下载已取消。")
            return
        self._status.setText(f"下载失败：{error}")
        QMessageBox.critical(self, "模型下载失败", error)

    def _on_download_finished(self, worker: _DownloadWorker, model_id: str) -> None:
        status_message = self._status.text()
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()
        self._progress.hide()
        self.refresh_models(preferred_id=model_id, status_message=status_message)
        self._show_pending_download_completion()

    def _show_pending_download_completion(self) -> None:
        completion = self._pending_download_completion
        if completion is None or self.isMinimized() or self._restore_on_application_activate:
            return
        title, _path = completion
        self._pending_download_completion = None
        message = QMessageBox(self)
        message.setObjectName("downloadCompleteMessage")
        message.setIcon(QMessageBox.Icon.Information)
        message.setWindowTitle("模型下载完成")
        message.setText(f"“{title}”已下载完成，并已设为当前模型。")
        message.setInformativeText("现在可以关闭模型管理页面，继续使用本地语义检索。")
        message.setStandardButtons(QMessageBox.StandardButton.Ok)
        message.setWindowModality(Qt.WindowModality.WindowModal)
        message.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        message.finished.connect(self._on_completion_message_finished)
        self._completion_message = message
        message.open()

    def _on_completion_message_finished(self, _result: int) -> None:
        self._completion_message = None

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            return
        if not self._restore_on_application_activate and not self.isMinimized():
            return
        self._restore_on_application_activate = False
        QTimer.singleShot(0, self._restore_after_application_activate)

    def _restore_after_application_activate(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._show_pending_download_completion()

    def _disconnect_application_activation(self, _result: int) -> None:
        if self._application is None:
            return
        try:
            self._application.applicationStateChanged.disconnect(self._on_application_state_changed)
        except TypeError:
            pass

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self._restore_on_application_activate = True

    def _use_selected(self) -> None:
        state = self._current_state()
        if state is None or not bool(state.installed):
            return
        model_id = str(state.preset.id)
        try:
            path = self._store.installed_path(model_id)
        except Exception as exc:
            QMessageBox.critical(self, "无法选择模型", str(exc))
            return
        if path is None:
            QMessageBox.warning(self, "无法选择模型", "模型安装不完整，请重新下载。")
            return
        self._selected_model_path = Path(path)
        self._selected_model_id = model_id
        self._status.setText(f"已将 {state.preset.title} 设为当前模型。")
        self._update_actions()

    def _remove_selected(self) -> None:
        state = self._current_state()
        if state is None or not bool(state.installed) or self._worker is not None:
            return
        answer = QMessageBox.question(
            self,
            "删除本地模型",
            f"确定删除“{state.preset.title}”的本地文件吗？需要时可以重新下载。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        model_id = str(state.preset.id)
        was_current = self._path_matches_selected(state)
        if was_current and self._on_before_remove_current is not None:
            self._on_before_remove_current()
        try:
            self._store.remove(model_id)
        except Exception as exc:
            QMessageBox.critical(self, "模型删除失败", str(exc))
            return
        if was_current:
            self._selected_model_path = None
            self._selected_model_id = None
        self.refresh_models(preferred_id=model_id, status_message=f"已删除 {state.preset.title}。")

    def accept(self) -> None:
        if self._worker is not None:
            self._status.setText("下载正在进行，请先等待完成或取消下载。")
            return
        super().accept()

    def reject(self) -> None:
        if self._worker is not None:
            self._status.setText("下载正在进行，请先等待完成或取消下载。")
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if self._worker is not None:
            self._status.setText("下载正在进行，请先等待完成或取消下载。")
            event.ignore()
            return
        super().closeEvent(event)


__all__ = ["EmbeddingModelManagerDialog", "LocalEmbeddingGuideDialog"]
