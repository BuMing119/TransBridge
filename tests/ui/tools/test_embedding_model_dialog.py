from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QLabel, QListWidget, QMessageBox, QProgressBar, QPushButton

from transbridge.ui.tools.ai_translator.embedding_model_dialog import (
    EmbeddingModelManagerDialog,
    LocalEmbeddingGuideDialog,
)


@dataclass(frozen=True)
class _Preset:
    id: str
    title: str
    description: str
    dimension: int
    download_size_mb: float
    recommended: bool = False


@dataclass(frozen=True)
class _State:
    preset: _Preset
    path: Path | None
    installed: bool


class _FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths: dict[str, Path] = {"installed": root / "installed-model"}
        self.removed: list[str] = []
        self.download_started = Event()
        self.release_download = Event()

    def list_models(self) -> list[_State]:
        presets = (
            _Preset("recommended", "轻量中文模型", "适合大多数本地术语库", 384, 86, True),
            _Preset("installed", "多语言模型", "覆盖多种语言", 768, 410),
        )
        return [_State(preset, self.paths.get(preset.id), preset.id in self.paths) for preset in presets]

    def installed_path(self, model_id: str) -> Path | None:
        return self.paths.get(model_id)

    def download(self, model_id: str, progress=None, cancelled=None) -> Path:
        self.download_started.set()
        if progress is not None:
            progress(1, 2, "正在下载模型文件（已完成 1/2）…")
            progress(0, -1, "正在下载模型文件… 已接收 5.0 MB")
        self.release_download.wait(2)
        if cancelled is not None and cancelled():
            raise RuntimeError("下载已取消")
        path = self.root / f"{model_id}-model"
        self.paths[model_id] = path
        if progress is not None:
            progress(2, 2, "正在下载模型文件（已完成 2/2）…")
        return path

    def remove(self, model_id: str) -> None:
        self.removed.append(model_id)
        self.paths.pop(model_id, None)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _process_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    app = _app()
    while time.monotonic() < deadline and not predicate():
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def test_local_guide_explains_degraded_behavior_and_reject_means_disable() -> None:
    app = _app()
    dialog = LocalEmbeddingGuideDialog()

    explanation = dialog.findChild(QLabel, "guideExplanation")
    note = dialog.findChild(QLabel, "guideNote")
    assert explanation is not None
    assert note is not None
    assert "语义检索服务暂时不可用" in explanation.text()
    assert "翻译和字面术语匹配" in explanation.text()
    assert note.foregroundRole() == QPalette.ColorRole.PlaceholderText
    assert note.property("tbSecondary") is True
    assert note.styleSheet() == ""

    dialog.reject()
    assert dialog.decision == "disable"
    assert dialog.result() == dialog.DialogCode.Rejected
    dialog.deleteLater()
    app.processEvents()


def test_local_guide_only_returns_configure_for_explicit_action() -> None:
    app = _app()
    dialog = LocalEmbeddingGuideDialog()
    configure = dialog.findChild(QPushButton, "configureButton")
    assert configure is not None

    configure.click()

    assert dialog.decision == "configure"
    assert dialog.result() == dialog.DialogCode.Accepted
    dialog.deleteLater()
    app.processEvents()


def test_manager_lists_metadata_and_selects_installed_model(tmp_path: Path) -> None:
    app = _app()
    store = _FakeStore(tmp_path)
    dialog = EmbeddingModelManagerDialog(store)

    model_list = dialog.findChild(QListWidget, "modelList")
    assert model_list is not None
    assert model_list.count() == 2
    assert "推荐" in model_list.item(0).text()
    assert "384 维" in model_list.item(0).text()
    assert "已安装" in model_list.item(1).text()

    model_list.setCurrentRow(1)
    use = dialog.findChild(QPushButton, "useButton")
    assert use is not None and use.isEnabled()
    use.click()

    assert dialog.selected_model_id == "installed"
    assert dialog.selected_model_path == tmp_path / "installed-model"
    assert use.text() == "当前使用"
    dialog.close()
    app.processEvents()


def test_manager_download_is_async_and_auto_selects_result(tmp_path: Path) -> None:
    app = _app()
    store = _FakeStore(tmp_path)
    dialog = EmbeddingModelManagerDialog(store)
    download = dialog.findChild(QPushButton, "downloadButton")
    close = dialog.findChild(QPushButton, "closeButton")
    assert download is not None and close is not None

    download.click()
    assert store.download_started.wait(1)
    app.processEvents()
    assert dialog._worker is not None and dialog._worker.isRunning()
    assert not close.isEnabled()
    assert dialog.result() == 0
    progress = dialog.findChild(QProgressBar, "progressBar")
    assert progress is not None
    _process_until(lambda: progress.maximum() == 2 and progress.value() == 1)
    assert "已接收 5.0 MB" in dialog._status.text()

    store.release_download.set()
    _process_until(lambda: dialog._worker is None)

    assert dialog.selected_model_id == "recommended"
    assert dialog.selected_model_path == tmp_path / "recommended-model"
    assert close.isEnabled()
    assert "已安装" in dialog._model_list.item(0).text()
    message = dialog.findChild(QMessageBox, "downloadCompleteMessage")
    assert message is not None
    assert message.isVisible()
    assert "轻量中文模型" in message.text()
    assert "已设为当前模型" in message.text()
    message.accept()
    _process_until(lambda: dialog._completion_message is None)
    dialog.close()
    app.processEvents()


def test_minimized_manager_restores_and_shows_deferred_completion_on_application_activate(tmp_path: Path) -> None:
    app = _app()
    store = _FakeStore(tmp_path)
    dialog = EmbeddingModelManagerDialog(store)
    dialog.show()
    app.processEvents()
    dialog.findChild(QPushButton, "downloadButton").click()
    assert store.download_started.wait(1)
    dialog.showMinimized()
    app.processEvents()
    assert dialog.isMinimized()

    store.release_download.set()
    _process_until(lambda: dialog._worker is None)

    assert dialog._pending_download_completion is not None
    assert dialog._completion_message is None
    dialog._on_application_state_changed(Qt.ApplicationState.ApplicationActive)
    _process_until(lambda: not dialog.isMinimized() and dialog._completion_message is not None)

    assert dialog.isVisible()
    assert dialog._completion_message is not None
    assert "轻量中文模型" in dialog._completion_message.text()
    dialog._completion_message.accept()
    _process_until(lambda: dialog._completion_message is None)
    dialog.close()
    app.processEvents()


def test_manager_confirms_before_removing_and_clears_current_selection(tmp_path: Path, monkeypatch) -> None:
    app = _app()
    store = _FakeStore(tmp_path)
    removal_order: list[str] = []
    original_remove = store.remove

    def remove(model_id: str) -> None:
        removal_order.append("remove")
        original_remove(model_id)

    monkeypatch.setattr(store, "remove", remove)
    current = tmp_path / "installed-model"
    dialog = EmbeddingModelManagerDialog(
        store,
        current_model_path=current,
        on_before_remove_current=lambda: removal_order.append("disable"),
    )
    dialog._model_list.setCurrentRow(1)
    remove = dialog.findChild(QPushButton, "removeButton")
    assert remove is not None

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.No)
    remove.click()
    assert store.removed == []

    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)
    remove.click()

    assert store.removed == ["installed"]
    assert removal_order == ["disable", "remove"]
    assert dialog.selected_model_id is None
    assert dialog.selected_model_path is None
    assert "未安装" in dialog._model_list.item(1).text()
    dialog.close()
    app.processEvents()


def test_manager_reject_does_not_destroy_a_running_download(tmp_path: Path) -> None:
    app = _app()
    store = _FakeStore(tmp_path)
    dialog = EmbeddingModelManagerDialog(store)
    dialog.findChild(QPushButton, "downloadButton").click()
    assert store.download_started.wait(1)

    dialog.reject()
    app.processEvents()

    assert dialog.result() == 0
    assert dialog._worker is not None and dialog._worker.isRunning()
    store.release_download.set()
    _process_until(lambda: dialog._worker is None)
    dialog.close()
    app.processEvents()
