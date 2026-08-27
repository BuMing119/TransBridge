"""Coordinate local embedding-model UI state without expanding the main window."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from PyQt6.QtWidgets import QMessageBox, QWidget


class _EmbeddingViewPort(Protocol):
    def update_embedding_controls(self) -> None: ...


class _ConfigPresenter(Protocol):
    def save(self) -> object: ...


class EmbeddingWindowCallbacks:
    """Route Embedding view intents to its controller and all other intents to the window."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def __getattr__(self, name: str) -> Any:
        return getattr(self._window, name)

    def on_embed_provider_changed(self) -> None:
        self._window._view_port.update_embedding_controls()

    def on_embedding_mode_activated(self) -> None:
        self._window._embedding_models.on_mode_activated()

    def on_embedding_api_provider_activated(self, _index: int) -> None:
        self._window._embedding_models.on_api_provider_activated()

    def on_manage_embedding_models(self) -> bool:
        return self._window._embedding_models.manage_models()


class EmbeddingModelController:
    """Own guide, manager, selection validation, and disabled-state persistence."""

    def __init__(
        self,
        parent: QWidget,
        view: Any,
        view_port: _EmbeddingViewPort,
        config_presenter: _ConfigPresenter,
        update_quick_run: Callable[[], None],
    ) -> None:
        self._parent = parent
        self._view = view
        self._view_port = view_port
        self._config_presenter = config_presenter
        self._update_quick_run = update_quick_run

    def on_mode_activated(self) -> None:
        if self._view.controls.embed_provider_combo.currentData() == "local":
            self.resolve_missing()

    def on_api_provider_activated(self) -> None:
        controls = self._view.controls
        if controls.embed_api_provider_combo.currentData() != "openai":
            return
        if not controls.embed_model_edit.text().strip():
            controls.embed_model_edit.setText("text-embedding-3-small")
        if not controls.embed_baseurl_edit.text().strip():
            controls.embed_baseurl_edit.setText("https://api.openai.com/v1")

    def manage_models(self) -> bool:
        from transbridge.infra.embedding_model_store import EmbeddingModelStore

        from .embedding_model_dialog import EmbeddingModelManagerDialog

        controls = self._view.controls
        current_path = controls.embed_local_model_edit.text().strip() or None
        try:
            store = EmbeddingModelStore()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(
                self._parent,
                "无法读取模型目录",
                f"本地向量模型目录配置无效，请修正配置后重试。\n\n{exc}",
            )
            return False
        dialog = EmbeddingModelManagerDialog(
            store,
            current_model_path=current_path,
            parent=self._parent,
            on_before_remove_current=self.disable,
        )
        dialog.exec()
        selected = dialog.selected_model_path
        selected_id = dialog.selected_model_id
        if selected is not None and selected_id:
            controls.embed_local_model_id_edit.setText(selected_id)
            controls.embed_local_model_edit.setText(str(selected))
            local_index = controls.embed_provider_combo.findData("local")
            controls.embed_provider_combo.setCurrentIndex(local_index)
            self._view_port.update_embedding_controls()
            self._config_presenter.save()
            self._update_quick_run()
            return True
        if not self.has_valid_model():
            controls.embed_local_model_id_edit.clear()
            controls.embed_local_model_edit.clear()
            self.disable()
            return False
        return True

    def resolve_missing(self) -> bool:
        if self.has_valid_model():
            return True
        # A modal guide runs its own event loop. Persist disabled before opening it
        # so an autosave timer cannot commit an enabled-but-unusable local mode.
        self.disable()
        from .embedding_model_dialog import LocalEmbeddingGuideDialog

        guide = LocalEmbeddingGuideDialog(self._parent)
        guide.exec()
        if guide.decision == "configure" and self.manage_models():
            return True
        return False

    def has_valid_model(self) -> bool:
        model_id = self._view.controls.embed_local_model_id_edit.text().strip()
        if not model_id:
            return False
        from transbridge.infra.embedding_model_store import EmbeddingModelStore

        try:
            return EmbeddingModelStore().installed_path(model_id) is not None
        except (KeyError, OSError, ValueError):
            return False

    def restore_managed_path(self) -> None:
        controls = self._view.controls
        model_id = controls.embed_local_model_id_edit.text().strip()
        local_requested = controls.embed_provider_combo.currentData() == "local"
        model_path = self._installed_path(model_id) if model_id else None
        controls.embed_local_model_edit.setText("" if model_path is None else str(model_path))
        if model_path is None and local_requested:
            disabled_index = controls.embed_provider_combo.findData("disabled")
            controls.embed_provider_combo.setCurrentIndex(disabled_index)
        self._view_port.update_embedding_controls()
        if model_path is None and local_requested:
            self._config_presenter.save()

    def disable(self) -> None:
        controls = self._view.controls
        disabled_index = controls.embed_provider_combo.findData("disabled")
        controls.embed_provider_combo.setCurrentIndex(disabled_index)
        self._view_port.update_embedding_controls()
        self._config_presenter.save()
        self._update_quick_run()

    @staticmethod
    def _installed_path(model_id: str) -> object | None:
        from transbridge.infra.embedding_model_store import EmbeddingModelStore

        try:
            return EmbeddingModelStore().installed_path(model_id)
        except (KeyError, OSError, ValueError):
            return None


__all__ = ["EmbeddingModelController", "EmbeddingWindowCallbacks"]
