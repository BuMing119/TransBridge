from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.ui_preferences import ThemeMode, UiPreferenceRepository
from transbridge.paratranz.config_manager import LLMConfig
from transbridge.ui.foundation.adapters import ThemeView
from transbridge.ui.foundation.builtins import DEFAULT_THEME_ID, create_builtin_registry
from transbridge.ui.foundation.theme_service import ThemePreference, ThemeService
from transbridge.ui.tools.ai_translator import config_presenter as config_module
from transbridge.ui.tools.ai_translator._translation_report_dialog import _TranslationReportDialog
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _theme(qapp: QApplication, tmp_path: Path) -> tuple[ThemeService, ThemeView]:
    path = tmp_path / "ui.ini"
    preferences = UiPreferenceRepository(
        ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    )
    service = ThemeService(qapp, create_builtin_registry(), preferences)
    service.start()
    service.set_preference(ThemePreference(ThemeMode.LIGHT, DEFAULT_THEME_ID), persist=False)
    return service, ThemeView(service)


def test_theme_revision_preserves_run_scope_inputs_report_and_subscription_lifecycle(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = LLMConfig(api_key="test-key", model="test-model")
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: config)
    context = SimpleNamespace(
        slots={},
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: (), locate_entry=lambda _entry_id: None)
    service, theme_view = _theme(qapp, tmp_path)
    baseline_subscriptions = theme_view.active_subscription_count
    window = AITranslatorWindow(context, workbench, theme_view=theme_view)
    report_stats = {"total": 3, "accepted": 2, "rejected": 1, "failed": 0, "avg_confidence": 0.8}
    report = _TranslationReportDialog(polish_stats=report_stats, theme_view=theme_view)
    window._view.controls.model_edit.setText("unsaved-model-draft")
    scope_before = window._scope_presenter.state
    request = window._run_controller.begin(
        "translate",
        config,
        [SimpleNamespace(id="entry-1", key="key-1")],
        esp_path=None,
    )
    run_spec = request.spec
    report_identity = id(report._polish_stats)
    subscriptions = theme_view.active_subscription_count

    changed = service.set_preference(ThemePreference(ThemeMode.DARK, DEFAULT_THEME_ID), persist=False)
    qapp.processEvents()

    assert changed.snapshot is not None
    assert window._theme_binding.revision == changed.snapshot.revision
    assert report.theme_revision == changed.snapshot.revision
    assert window._view.controls.model_edit.text() == "unsaved-model-draft"
    assert window._scope_presenter.state == scope_before
    assert window._run_controller.active_request is request
    assert request.run_id == run_spec.run_id
    assert request.spec is run_spec
    assert id(report._polish_stats) == report_identity
    assert report._polish_stats == report_stats
    assert window._view.controls.preflight_label.accessibleName() == "AI 运行条件"
    assert theme_view.active_subscription_count == subscriptions

    window.close()
    report.close()
    report.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert theme_view.active_subscription_count == baseline_subscriptions
    theme_view.close()
    service.close()
