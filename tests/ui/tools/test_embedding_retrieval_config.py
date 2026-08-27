from __future__ import annotations

from copy import deepcopy
import threading
import time
from unittest.mock import patch

import numpy as np
from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget
import pytest

from transbridge.config.language_profiles import LanguageProfile
from transbridge.config.llm import EmbeddingConfig, LLMConfig
from transbridge.ui.tools.ai_translator import config_presenter as config_module, config_view as config_view_module
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter, ConnectionTestResult
from transbridge.ui.tools.ai_translator.config_view import AITranslatorView, WindowConfigView
from transbridge.ui.tools.ai_translator.embedding_connection_controller import EmbeddingConnectionController
from transbridge.ui.tools.ai_translator.embedding_model_controller import EmbeddingModelController
from transbridge.ui.tools.ai_translator.view_state import TranslatorViewPort

_APP = QApplication.instance() or QApplication([])


class _Callbacks:
    def __init__(self) -> None:
        self.view_port: TranslatorViewPort | None = None

    def on_provider_changed(self) -> None:
        return None

    def on_embed_provider_changed(self) -> None:
        if self.view_port is not None:
            self.view_port.update_embedding_controls()

    def on_embedding_mode_activated(self) -> None:
        return None

    def on_embedding_api_provider_activated(self, _index: int) -> None:
        return None

    def on_manage_embedding_models(self) -> None:
        return None

    def on_test_connection(self, _target: str = "llm") -> None:
        return None

    def browse_file(self, *_args) -> None:
        return None

    def on_view_terms(self) -> None:
        return None

    def on_open_history(self) -> None:
        return None

    def on_batch_start(self) -> None:
        return None

    def on_start(self) -> None:
        return None

    def on_mode_changed(self) -> None:
        return None

    def update_estimate(self) -> None:
        return None

    def update_quick_run(self) -> None:
        return None

    def on_pp_enable_changed(self) -> None:
        return None

    def on_polish_changed(self) -> None:
        return None


def _config_view():
    parent = QWidget()
    callbacks = _Callbacks()
    view = AITranslatorView(parent, callbacks)
    callbacks.view_port = TranslatorViewPort(view)
    return _APP, parent, view, WindowConfigView(view, callbacks, lambda: None)


def test_target_language_profiles_populate_and_round_trip_locale(monkeypatch) -> None:
    profiles = (
        LanguageProfile("ja_JP", "日本語", "English", "Japanese"),
        LanguageProfile("zh_CN", "中文（简体）", "English", "Simplified Chinese"),
    )
    monkeypatch.setattr(config_view_module, "discover_language_profiles", lambda: profiles)
    app, parent, view, adapter = _config_view()
    config = LLMConfig(target_lang="ja_JP")

    adapter.render_config(config)

    assert view.controls.target_lang_combo.count() == 2
    assert view.controls.target_lang_combo.currentData() == "ja_JP"
    assert "日本語" in view.controls.target_lang_combo.currentText()
    assert adapter.update_config(config).target_lang == "ja_JP"
    parent.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("mode", "provider"),
    (("disabled", "openai"), ("local", "local"), ("api", "openai")),
)
def test_embedding_mode_round_trips_between_config_and_view(mode: str, provider: str) -> None:
    app, parent, view, adapter = _config_view()
    config = LLMConfig(embedding=EmbeddingConfig(mode=mode, provider=provider))

    adapter.render_config(config)

    assert view.controls.embed_provider_combo.currentData() == mode
    updated = adapter.update_config(config)
    assert updated.embedding.mode == mode
    assert updated.embedding.provider == provider
    parent.close()
    app.processEvents()


@pytest.mark.parametrize(
    ("mode", "local_visible", "api_visible", "test_available"),
    (("disabled", False, False, False), ("local", True, False, False), ("api", False, True, True)),
)
def test_embedding_mode_controls_only_show_relevant_fields(
    mode: str,
    local_visible: bool,
    api_visible: bool,
    test_available: bool,
) -> None:
    app, parent, view, adapter = _config_view()
    config = LLMConfig(embedding=EmbeddingConfig(mode=mode, provider="local" if mode == "local" else "openai"))

    adapter.render_config(config)

    assert view.controls.embed_local_status_label.isHidden() is (not local_visible)
    assert view.controls.embed_manage_btn.isHidden() is (not local_visible)
    assert view.controls.embed_api_provider_combo.isHidden() is (not api_visible)
    assert view.controls.embed_model_edit.isHidden() is (not api_visible)
    assert view.controls.embed_apikey_edit.isHidden() is (not api_visible)
    assert view.controls.embed_baseurl_edit.isHidden() is (not api_visible)
    assert view.controls.embed_test_btn.isHidden() is (not test_available)
    assert view.controls.embed_test_btn.isEnabled() is test_available
    parent.close()
    app.processEvents()


class _AsyncEmbeddingPresenter:
    def __init__(self) -> None:
        self.config = object()
        self.started = threading.Event()
        self.release = threading.Event()
        self.build_calls = 0
        self.connection_thread: QThread | None = None

    def build(self) -> object:
        self.build_calls += 1
        return self.config

    def test_embedding_connection(self, config: object | None = None) -> ConnectionTestResult:
        assert config is self.config
        self.connection_thread = QThread.currentThread()
        self.started.set()
        if not self.release.wait(2):
            raise RuntimeError("test did not release the connection worker")
        return ConnectionTestResult("info", "语义检索可用", "API 服务编码成功，向量维度 8。")


def _process_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError("Qt condition was not reached before timeout")


def test_embedding_api_connection_runs_in_background_and_restores_button() -> None:
    app, parent, view, adapter = _config_view()
    adapter.render_config(LLMConfig(embedding=EmbeddingConfig(mode="api", provider="openai")))
    presenter = _AsyncEmbeddingPresenter()
    results: list[ConnectionTestResult] = []
    controller = EmbeddingConnectionController(view, presenter, results.append)

    before = time.monotonic()
    assert controller.start() is True
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert presenter.started.wait(1)
    assert presenter.connection_thread is not app.thread()
    assert view.controls.embed_test_btn.text() == "正在测试…"
    assert not view.controls.embed_test_btn.isEnabled()
    assert controller.start() is False
    presenter.release.set()
    _process_until(lambda: bool(results) and not controller.is_running)

    assert results[0].level == "info"
    assert presenter.build_calls == 1
    assert view.controls.embed_test_btn.text() == "测试 Embedding 连接"
    assert view.controls.embed_test_btn.isEnabled()
    controller.close()
    parent.close()
    app.processEvents()


def test_closing_embedding_connection_controller_does_not_wait_or_show_late_result() -> None:
    app, parent, view, adapter = _config_view()
    adapter.render_config(LLMConfig(embedding=EmbeddingConfig(mode="api", provider="openai")))
    presenter = _AsyncEmbeddingPresenter()
    results: list[ConnectionTestResult] = []
    controller = EmbeddingConnectionController(view, presenter, results.append)
    assert controller.start() is True
    assert presenter.started.wait(1)

    before = time.monotonic()
    controller.close()
    elapsed = time.monotonic() - before

    assert elapsed < 0.1
    assert not controller.is_running
    presenter.release.set()
    _process_until(lambda: not _active_embedding_workers())
    assert results == []
    parent.close()
    app.processEvents()


@pytest.mark.parametrize("mode", ["disabled", "local"])
def test_embedding_connection_controller_rejects_non_api_modes(mode: str) -> None:
    app, parent, view, adapter = _config_view()
    adapter.render_config(LLMConfig(embedding=EmbeddingConfig(mode=mode, provider=mode)))
    presenter = _AsyncEmbeddingPresenter()
    controller = EmbeddingConnectionController(view, presenter, lambda _result: None)

    assert controller.start() is False
    assert presenter.build_calls == 0
    assert view.controls.embed_test_btn.isHidden()
    assert not view.controls.embed_test_btn.isEnabled()

    controller.close()
    parent.close()
    app.processEvents()


def _active_embedding_workers() -> bool:
    from transbridge.ui.tools.ai_translator.embedding_connection_controller import _ACTIVE_WORKERS

    return bool(_ACTIVE_WORKERS)


def test_openai_embedding_provider_fills_only_empty_recommended_fields() -> None:
    app, parent, view, _adapter = _config_view()
    controls = view.controls
    controls.embed_api_provider_combo.setCurrentIndex(controls.embed_api_provider_combo.findData("openai"))
    controls.embed_model_edit.clear()
    controls.embed_baseurl_edit.clear()
    controller = EmbeddingModelController(
        parent,
        view,
        TranslatorViewPort(view),
        type("Presenter", (), {"save": lambda _self: None})(),
        lambda: None,
    )

    controller.on_api_provider_activated()

    assert controls.embed_model_edit.text() == "text-embedding-3-small"
    assert controls.embed_baseurl_edit.text() == "https://api.openai.com/v1"
    controls.embed_model_edit.setText("custom-model")
    controls.embed_baseurl_edit.setText("https://embedding.example/v1")
    controller.on_api_provider_activated()
    assert controls.embed_model_edit.text() == "custom-model"
    assert controls.embed_baseurl_edit.text() == "https://embedding.example/v1"
    parent.close()
    app.processEvents()


def test_startup_normalizes_missing_managed_local_model_to_disabled() -> None:
    app, parent, view, adapter = _config_view()
    adapter.render_config(LLMConfig(embedding=EmbeddingConfig(mode="local", provider="local")))
    saves: list[None] = []
    controller = EmbeddingModelController(
        parent,
        view,
        TranslatorViewPort(view),
        type("Presenter", (), {"save": lambda _self: saves.append(None)})(),
        lambda: None,
    )

    controller.restore_managed_path()

    assert view.controls.embed_provider_combo.currentData() == "disabled"
    assert view.controls.embed_local_model_edit.text() == ""
    assert saves == [None]
    parent.close()
    app.processEvents()


def test_model_manager_reports_an_invalid_catalog_without_opening_dialog() -> None:
    app, parent, view, _adapter = _config_view()
    controller = EmbeddingModelController(
        parent,
        view,
        TranslatorViewPort(view),
        type("Presenter", (), {"save": lambda _self: None})(),
        lambda: None,
    )

    with (
        patch("transbridge.infra.embedding_model_store.EmbeddingModelStore", side_effect=ValueError("bad catalog")),
        patch.object(QMessageBox, "critical") as critical,
    ):
        assert controller.manage_models() is False

    assert "配置无效" in critical.call_args.args[2]
    assert "bad catalog" in critical.call_args.args[2]
    parent.close()
    app.processEvents()


class _PresenterView:
    def render_config(self, _config) -> None:
        return None

    def update_config(self, config):
        return config


class _EmbeddingClient:
    available = True
    error_message = None

    def encode(self, _texts):
        return np.ones((1, 8), dtype="float32")


def _presenter_for(monkeypatch, config: LLMConfig) -> ConfigPresenter:
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: deepcopy(config))
    return ConfigPresenter(_PresenterView())


def test_embedding_check_reports_disabled_without_constructing_client(monkeypatch) -> None:
    presenter = _presenter_for(monkeypatch, LLMConfig())

    with patch("transbridge.infra.embedding_client.create_embedding_client") as factory:
        result = presenter.test_embedding_connection()

    assert result.level == "info"
    assert "已关闭" in result.title
    factory.assert_not_called()


def test_embedding_check_validates_api_configuration_before_network(monkeypatch) -> None:
    config = LLMConfig(api_key="", base_url="")
    config.embedding = EmbeddingConfig(mode="api", provider="openai", api_key="", base_url="", model="")
    presenter = _presenter_for(monkeypatch, config)

    result = presenter.test_embedding_connection()

    assert result.level == "warning"
    assert "API Key" in result.message


def test_embedding_check_executes_one_minimal_encoding(monkeypatch) -> None:
    config = LLMConfig(api_key="llm-key", base_url="https://llm.example/v1")
    config.embedding = EmbeddingConfig(
        mode="api",
        provider="openai",
        api_key="embedding-key",
        base_url="https://embedding.example/v1",
        model="embedding-model",
    )
    presenter = _presenter_for(monkeypatch, config)

    with patch("transbridge.infra.embedding_client.create_embedding_client", return_value=_EmbeddingClient()):
        result = presenter.test_embedding_connection()

    assert result.level == "info"
    assert "向量维度 8" in result.message


def test_embedding_check_does_not_reuse_llm_credentials(monkeypatch) -> None:
    config = LLMConfig(api_key="llm-key", base_url="https://llm.example/v1")
    config.embedding = EmbeddingConfig(mode="api", provider="openai", model="embedding-model")
    presenter = _presenter_for(monkeypatch, config)

    result = presenter.test_embedding_connection()

    assert result.level == "warning"
    assert "独立" in result.message


def test_managed_local_model_persists_stable_id_instead_of_derived_path() -> None:
    app, parent, view, adapter = _config_view()
    config = LLMConfig(
        embedding=EmbeddingConfig(
            mode="local",
            provider="local",
            local_model_id="multilingual-minilm-l12-v2",
        )
    )

    adapter.render_config(config)
    view.controls.embed_local_model_edit.setText("C:/derived/runtime/path")
    updated = adapter.update_config(config)

    assert updated.embedding.local_model_id == "multilingual-minilm-l12-v2"
    assert updated.embedding.local_model_path == ""
    parent.close()
    app.processEvents()
