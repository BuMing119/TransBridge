from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository
from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.projection_types import CollectionSlot
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow


@pytest.fixture
def task_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    path = tmp_path / "config.ini"
    repository = ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    config = LLMConfig(model="demo")
    config.save_to_file(repository=repository)
    loaded = LLMConfig.load_from_file(repository=repository, environment={})
    profiles = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    profiles.upsert(CustomWorkflowProfile.from_config("Test profile", "polish", config), select=True)
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: loaded)
    monkeypatch.setattr(
        "transbridge.ui.tools.ai_translator.custom_profile_presenter.AiWorkflowProfileRepository", lambda: profiles
    )
    entries = [
        TranslationEntry(str(i), str(i), "Source", "" if i < 11 else "Translation", 0 if i < 11 else 1, "NPC_:FULL")
        for i in range(200)
    ]
    slot = CollectionSlot("Demo", TranslationEntryCollection(entries))
    ctx = SimpleNamespace(
        slots={"demo": slot},
        active_slot=slot,
        collection=slot.collection,
        esp_path=None,
        current_project=None,
        entry_labels={},
        label_library={},
    )
    step = SimpleNamespace(filtered_entries=lambda: entries, locate_entry=lambda _: None)

    class Window(AITranslatorWindow):
        switches = 0

        def on_mode_changed(self):
            self.switches += 1
            super().on_mode_changed()

    before = path.read_bytes()
    window = Window(ctx, step)
    window.show()
    app.processEvents()
    yield app, window
    window.close()
    app.processEvents()
    assert path.read_bytes() == before


def _track_scope(monkeypatch, window):
    calls = []
    build = window._task_scope.build

    def tracked(*args, **kwargs):
        calls.append(kwargs["mode"])
        return build(*args, **kwargs)

    monkeypatch.setattr(window._task_scope, "build", tracked)
    return calls


def test_switch_runs_once_and_reuses_one_scope_for_estimate_and_preflight(task_window, monkeypatch):
    app, window = task_window
    calls = _track_scope(monkeypatch, window)
    for mode in ("polish", "mixed", "custom", "translate"):
        switches = window.switches
        calls.clear()
        getattr(window._view.controls, "mode_" + mode).click()
        assert window.switches == switches + 1
        assert calls == []  # Clicking does not synchronously traverse the collections.
        assert not window._view.controls.start_btn.isEnabled()
        app.processEvents()
        assert calls == [window._view_port.mode]
        count = 11 if mode == "translate" else 0 if mode == "mixed" else 189
        assert f"本次任务 {count}" in window._view.controls.estimate_lbl.text()
        assert f"本次 {count} 条" in window._view.sources_panel.summary.text()


def test_rapid_switches_only_evaluate_final_mode_and_close_discards_pending_refresh(task_window, monkeypatch):
    app, window = task_window
    calls = _track_scope(monkeypatch, window)
    for mode in ("polish", "mixed", "custom", "translate"):
        getattr(window._view.controls, "mode_" + mode).click()
    assert calls == []
    app.processEvents()
    assert calls == ["translate"]
    assert "本次任务 11" in window._view.controls.estimate_lbl.text()
    calls.clear()
    window._view.controls.mode_polish.click()
    window.close()
    app.processEvents()
    assert calls == []


def test_programmatic_config_hydration_emits_no_user_change_signals(task_window):
    _app, window = task_window
    changes = []
    controls = window._view.controls
    controls.concurrent_spin.valueChanged.connect(changes.append)
    controls.pp_enable_check.toggled.connect(changes.append)
    controls.pp_polish_check.toggled.connect(changes.append)
    cfg = window._config_presenter.build()
    cfg.max_concurrent += 1
    cfg.enable_post_process = not cfg.enable_post_process
    cfg.pp_enable_polish = not cfg.pp_enable_polish
    window._config_presenter._view.render_config(cfg)
    assert changes == []
    controls.concurrent_spin.setValue(cfg.max_concurrent + 1)
    assert changes == [cfg.max_concurrent + 1]  # User edits remain connected.


def _rect(widget, window):
    point = widget.mapTo(window, QPoint(0, 0))
    return point.x(), point.y(), widget.width(), widget.height()


@pytest.mark.parametrize("size,font_size", [((1120, 760), 10), ((900, 700), 14), ((720, 600), 10)])
def test_mode_switches_preserve_shell_and_common_field_anchors(task_window, size, font_size):
    app, window = task_window
    window.setFont(QFont("Sans Serif", font_size))
    window.resize(*size)
    app.processEvents()
    view = window._view
    assert window.width() == size[0]
    controls = view.controls
    anchored = (
        view._context_label,
        controls.mode_translate.parentWidget(),
        view.sources_panel,
        view._task_surface,
        controls.tabs,
        controls.start_btn.parentWidget(),
        controls.start_btn,
    )
    baseline = [_rect(widget, window) for widget in anchored]
    fields = (
        controls.target_lang_combo,
        view._scope_filter_box,
        controls.scope_stage_all_btn,
        controls.scope_label_all_btn,
        controls.scope_cat_all_btn,
        controls.overwrite_check,
    )
    field_origins = [_rect(widget, window)[:2] for widget in fields]
    for mode in ("polish", "mixed", "custom", "translate"):
        getattr(controls, "mode_" + mode).click()
        app.processEvents()
        app.processEvents()
        assert [_rect(widget, window) for widget in anchored] == baseline
        assert [_rect(widget, window)[:2] for widget in fields] == field_origins
        assert controls.tabs.widget(0).verticalScrollBar().value() == 0
        assert controls.overwrite_check.isVisible()
    assert controls.custom_profile_group.parentWidget() is controls.target_lang_combo.parentWidget()
