# See LICENSE and NOTICE for TransBridge licensing and attribution terms.
"""Bind the dictionary settings menu to application preferences and scoped main libraries."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QActionGroup
from PyQt6.QtWidgets import QApplication, QMenu

from transbridge.application.projects.dictionary_apply import dictionary_library
from transbridge.config.dictionary_preferences import DictionaryPreferences, dictionary_preferences
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.workers import ApiWorker

_logger = logging.getLogger(__name__)


class _PreferenceChanges(QObject):
    changed = pyqtSignal()

    def __init__(self, parent):
        super().__init__(parent)
        self._worker: ApiWorker | None = None

    @property
    def busy(self) -> bool:
        return self._worker is not None

    def save(self, preferences: DictionaryPreferences, name: str, enabled: bool) -> None:
        if self.busy:
            return
        pending = preferences.stage_set(name, enabled)
        worker = None
        try:
            worker = ApiWorker(pending.commit, report_api_status=False, route_http_errors=False)
            worker.finished.connect(self._settled)
            worker.finished.connect(worker.deleteLater)
            self._worker = worker
            worker.start()
        except Exception:
            pending.cancel()
            self._worker = None
            if worker is not None:
                worker.deleteLater()
            raise

    @pyqtSlot()
    def _settled(self) -> None:
        self._worker = None
        self.changed.emit()


def _preference_changes() -> _PreferenceChanges:
    app = QApplication.instance()
    changes = getattr(app, "_dictionary_preference_changes", None)
    if changes is None:
        changes = _PreferenceChanges(app)
        app._dictionary_preference_changes = changes
    return changes


@dataclass(frozen=True, slots=True)
class _MainScope:
    key: tuple[str | None, Path, str, str, str]
    library: DictionaryLibrary


def _read_mains(library: DictionaryLibrary) -> tuple[tuple[str, ...], str]:
    return tuple(library.list_mains()), library.active_main()


class DictionarySettingsMenu(QMenu):
    def __init__(self, parent: QMenu, gettext):
        super().__init__(gettext("翻译词典"), parent)
        self._context = None
        self._scope: _MainScope | None = None
        self._worker: ApiWorker | None = None
        self._worker_scope: _MainScope | None = None
        self._names: tuple[str, ...] = ()
        self._active_main = ""
        self._no_main_label = gettext("不选择主库")

        self._preferences = (
            (self.addAction(gettext("保存工程时将译文存入词典")), "save_on_project_save"),
            (self.addAction(gettext("打开插件时自动套用词典")), "apply_on_plugin_open"),
        )
        for action, preference in self._preferences:
            action.setCheckable(True)
            action.setChecked(True)
            action.triggered.connect(lambda checked, name=preference: self._set_preference(name, checked))

        self._main_menu = self.addMenu(gettext("主库"))
        self._main_menu.menuAction().setVisible(False)
        self._main_group = QActionGroup(self._main_menu)
        self._main_group.triggered.connect(self._select_main)
        self.aboutToShow.connect(self._sync_preferences)
        self.aboutToShow.connect(self._refresh_mains)

    def bind_context(self, context) -> None:
        self._context = context
        _preference_changes().changed.connect(self._sync_preferences)
        for name in ("project_changed", "project_installed", "text_encodings_changed"):
            if (signal := getattr(context, name, None)) is not None:
                signal.connect(self._scope_changed)

    @pyqtSlot()
    def _sync_preferences(self) -> None:
        if self._context is None:
            return
        try:
            preferences = dictionary_preferences(self._context)
            enabled = not _preference_changes().busy
            for action, name in self._preferences:
                action.setChecked(getattr(preferences, name))
                action.setEnabled(enabled)
        except Exception:
            _logger.exception("无法读取翻译词典偏好")
            for action, _name in self._preferences:
                action.setEnabled(False)

    def _set_preference(self, name: str, enabled: bool) -> None:
        if self._context is None:
            return
        changes = _preference_changes()
        try:
            changes.save(dictionary_preferences(self._context), name, enabled)
        except Exception:
            _logger.exception("无法保存翻译词典偏好：%s", name)
        changes.changed.emit()

    def _sync_scope(self) -> _MainScope | None:
        if self._context is None:
            return None
        try:
            library = dictionary_library(self._context)
        except Exception:
            _logger.exception("无法确定当前主库范围")
            self._scope = None
            self._show_mains((), "")
            return None
        key = (
            getattr(self._context, "active_project_id", None),
            library.base_dir,
            library.game,
            library.source_locale,
            library.target_locale,
        )
        if self._scope is None or self._scope.key != key:
            self._scope = _MainScope(key, library)
            self._show_mains((), "")
        return self._scope

    @pyqtSlot()
    def _scope_changed(self) -> None:
        if self.isVisible():
            self._refresh_mains()
        elif self._scope is not None:
            self._sync_scope()

    @pyqtSlot()
    def _refresh_mains(self) -> None:
        scope = self._sync_scope()
        if scope is not None and self._worker is None:
            self._start_worker(scope, _read_mains, scope.library, reading=True)

    def _select_main(self, action) -> None:
        name = action.data()
        previous_scope = self._scope
        scope = self._sync_scope()
        if scope is None or scope is not previous_scope or getattr(self._context, "project_opening", False):
            _logger.warning("主库范围已变化，请重新打开翻译词典设置")
            self._restore_checks()
            return
        self._restore_checks()
        if self._worker is not None or name == self._active_main:
            return
        self._start_worker(scope, self._persist_main, scope, name, self._names)

    def _persist_main(self, scope: _MainScope, name: str, names: tuple[str, ...]) -> tuple[tuple[str, ...], str] | None:
        committed = False

        def still_current():
            return not getattr(self._context, "project_opening", False) and self._sync_scope() is scope

        def commit(publish):
            nonlocal committed
            if ExecutionContext.call_on_app_thread(self._context, still_current):
                publish()
                committed = True
            else:
                _logger.warning("主库范围已变化，未保存所选主库")

        scope.library.select_main(name, commit=commit)
        return (names, name) if committed else None

    def _start_worker(self, scope: _MainScope, operation, *args, reading=False) -> None:
        worker = None
        try:
            worker = ApiWorker(operation, *args, report_api_status=False, route_http_errors=False)
            worker.result.connect(self._loaded)
            if reading:
                worker.error.connect(self._read_failed)
            worker.finished.connect(self._settled)
            worker.finished.connect(worker.deleteLater)
            self._worker, self._worker_scope = worker, scope
            self._main_menu.setEnabled(False)
            worker.start()
        except Exception:
            _logger.exception("无法启动主库操作")
            if worker is not None:
                worker.deleteLater()
            self._worker = self._worker_scope = None
            self._main_menu.setEnabled(True)
            if reading:
                self._show_mains((), "")

    @pyqtSlot(object)
    def _loaded(self, result) -> None:
        if self._sync_scope() is self._worker_scope and result is not None:
            self._show_mains(*result)

    @pyqtSlot(str)
    def _read_failed(self, _message: str) -> None:
        if self._scope is self._worker_scope:
            self._show_mains((), "")

    @pyqtSlot()
    def _settled(self) -> None:
        scope = self._worker_scope
        self._worker = self._worker_scope = None
        self._main_menu.setEnabled(True)
        if scope is not self._scope and self.isVisible():
            self._refresh_mains()

    def _show_mains(self, names: tuple[str, ...], selected: str) -> None:
        if names != self._names:
            self._main_menu.clear()
            if names:
                for name in ("", *names):
                    label = name.replace("&", "&&") if name else self._no_main_label
                    action = self._main_menu.addAction(label)
                    action.setData(name)
                    action.setCheckable(True)
                    self._main_group.addAction(action)
        self._names, self._active_main = names, selected
        self._restore_checks()
        self._main_menu.menuAction().setVisible(bool(names))

    def _restore_checks(self) -> None:
        for action in self._main_group.actions():
            action.setChecked(action.data() == self._active_main)
