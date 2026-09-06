"""Background source read and naming-scheme creation flow."""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import QDialog, QMessageBox, QWidget


class _ReadSignals(QObject):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)


class _ReadTask(QRunnable):
    def __init__(self, reader_factory: Callable[[], object], request: object) -> None:
        super().__init__()
        self.signals = _ReadSignals()
        self._reader_factory = reader_factory
        self._request = request

    def run(self) -> None:
        try:
            snapshot = self._reader_factory().read(self._request)
        except Exception as exc:  # noqa: BLE001 - worker boundary returns an actionable UI error
            self.signals.failed.emit(str(exc))
        else:
            self.signals.succeeded.emit(snapshot)


class TerminologySourceImportController(QObject):
    """Keep slow I/O outside Qt while all profile mutations stay on the UI thread."""

    def __init__(
        self,
        parent: QWidget,
        button,
        profile_controller,
        reader_factory: Callable[[], object],
        *,
        idle_button_text: str = "从术语来源创建译名方案…",
    ) -> None:
        super().__init__(parent)
        self._parent = parent
        self._button = button
        self._profiles = profile_controller
        self._reader_factory = reader_factory
        self._idle_button_text = idle_button_text
        self._active_task: _ReadTask | None = None
        self._default_name = ""
        self._expected_identity: tuple[str, str] | None = None

    def start(self, request, *, default_name: str) -> None:
        self.start_with_reader(request, default_name=default_name)

    def start_with_reader(
        self,
        request,
        *,
        default_name: str,
        reader_factory: Callable[[], object] | None = None,
    ) -> None:
        if self._active_task is not None:
            return
        if self._profiles is None:
            QMessageBox.warning(self._parent, "无法创建译名方案", "请先打开一个已保存的工程翻译版本。")
            return
        self._expected_identity = self._profiles.identity
        if self._expected_identity is None:
            QMessageBox.warning(self._parent, "无法创建译名方案", "请先打开一个已保存的工程翻译版本。")
            return
        self._default_name = default_name
        self._button.setEnabled(False)
        self._button.setText("正在读取来源…")
        task = _ReadTask(reader_factory or self._reader_factory, request)
        task.signals.succeeded.connect(self._source_ready)
        task.signals.failed.connect(self._source_failed)
        self._active_task = task
        QThreadPool.globalInstance().start(task)

    def _source_ready(self, source) -> None:
        self._reset_button()
        if self._profiles.identity != self._expected_identity:
            QMessageBox.warning(self._parent, "工程已变化", "读取期间工程或翻译版本发生了变化，请重新操作。")
            return
        try:
            preview = self._profiles.preview_source_import(source)
        except Exception as exc:  # noqa: BLE001 - application error belongs in the user flow
            QMessageBox.warning(self._parent, "无法创建译名方案", str(exc))
            return
        from transbridge.ui.tools.terminology_profiles import TerminologySourceImportDialog

        dialog = TerminologySourceImportDialog(preview, self._default_name, self._parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = self._profiles.create_from_source_import(
                dialog.profile_name,
                preview,
                select=dialog.select_after_create,
            )
        except Exception as exc:  # noqa: BLE001 - persistence failures remain visible and recoverable
            QMessageBox.warning(self._parent, "创建译名方案失败", str(exc))
            return
        adopted = preview.matched_term_count - preview.conflict_count
        kept = preview.base_mapping_count - adopted
        suffix = "，并已设为当前方案" if result.selection is not None else "，现在可在译名方案中选择"
        QMessageBox.information(
            self._parent,
            "译名方案已创建",
            f"已创建“{result.profile.name}”（采用 {adopted} 个来源译名，{kept} 个保持当前译名）{suffix}。",
        )

    def _source_failed(self, message: str) -> None:
        self._reset_button()
        QMessageBox.warning(self._parent, "术语来源读取失败", message)

    def _reset_button(self) -> None:
        self._active_task = None
        self._button.setText(self._idle_button_text)
        self._button.setEnabled(self._profiles is not None)


__all__ = ["TerminologySourceImportController"]
