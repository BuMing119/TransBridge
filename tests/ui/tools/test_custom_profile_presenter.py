from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from transbridge.application.translation.custom_workflow_profile import (
    CustomWorkflowProfile,
    CustomWorkflowProfileDocument,
)
from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator import config_presenter as config_module
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
from transbridge.ui.tools.ai_translator.custom_profile_presenter import CustomProfilePresenter


class _ConfigView:
    def __init__(self) -> None:
        self.rendered: object | None = None
        self.max_concurrent: int | None = None
        self.enable_post_process: bool | None = None

    def render_config(self, config: object) -> None:
        self.rendered = config

    def update_config(self, config: object) -> object:
        if self.max_concurrent is not None:
            config.max_concurrent = self.max_concurrent
        if self.enable_post_process is not None:
            config.enable_post_process = self.enable_post_process
        return config


class _ProfileView:
    def __init__(self) -> None:
        self.document = CustomWorkflowProfileDocument.empty()
        self.error = ""

    def render_profiles(self, document: CustomWorkflowProfileDocument) -> None:
        self.document = document

    def render_profile_error(self, message: str) -> None:
        self.error = message


def _profile(name: str, *, base_mode: str = "polish", max_concurrent: int = 7) -> CustomWorkflowProfile:
    return CustomWorkflowProfile.create(
        name,
        base_mode=base_mode,  # type: ignore[arg-type]
        strategy="combined",
        workflow={"enable_post_process": True, "pp_polish_level": "light"},
        limits={
            "max_concurrent": max_concurrent,
            "max_tokens_per_batch": 2500,
            "max_output_tokens": 0,
            "max_terms_per_batch": 40,
        },
        mixed=None,
    )


def test_custom_profile_autosave_updates_profile_without_polluting_global_limits(tmp_path: Path) -> None:
    config_view = _ConfigView()
    config = ConfigPresenter(config_view)
    config.load()
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    profile_view = _ProfileView()
    profiles = CustomProfilePresenter(profile_view, config, repository)
    original_global = LLMConfig.load_from_file().max_concurrent

    created = profiles.create("校对", "polish")
    config_view.max_concurrent = 11
    config_view.enable_post_process = False
    execution = config.save()

    persisted = repository.load().get(created.id)
    assert persisted is not None
    assert persisted.limits["max_concurrent"] == 11
    assert persisted.workflow["enable_post_process"] is False
    assert execution.max_concurrent == 11
    assert LLMConfig.load_from_file().max_concurrent == original_global


def test_empty_custom_editor_does_not_overwrite_builtin_workflow_or_limits() -> None:
    config_view = _ConfigView()
    config = ConfigPresenter(config_view)
    config.load()
    original = LLMConfig.load_from_file()
    original_translate = dict(original.workflow_profiles["translate"])
    config.clear_custom()
    config_view.max_concurrent = original.max_concurrent + 10
    config_view.enable_post_process = not original.enable_post_process

    config.save()
    config.switch_preset("translate")
    restored = LLMConfig.load_from_file()

    assert restored.max_concurrent == original.max_concurrent
    assert restored.workflow_profiles["translate"] == original_translate


def test_select_flushes_previous_profile_then_activates_new_overlay(tmp_path: Path) -> None:
    config_view = _ConfigView()
    config = ConfigPresenter(config_view)
    config.load()
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    first = _profile("first", max_concurrent=4)
    second = _profile("second", max_concurrent=8)
    repository.save(CustomWorkflowProfileDocument(first.id, (first, second)))
    profiles = CustomProfilePresenter(_ProfileView(), config, repository)
    profiles.load()
    profiles.activate_selected()

    config_view.max_concurrent = 13
    profiles.select(second.id)

    document = repository.load()
    assert document.selected_profile_id == second.id
    assert document.get(first.id).limits["max_concurrent"] == 13  # type: ignore[union-attr]
    assert config.active_custom_profile is not None
    assert config.active_custom_profile.id == second.id
    config_view.max_concurrent = None
    assert config.build().max_concurrent == 8


def test_failed_import_keeps_current_selection_and_active_overlay(tmp_path: Path) -> None:
    config = ConfigPresenter(_ConfigView())
    config.load()
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    profile = _profile("current")
    repository.save(CustomWorkflowProfileDocument(profile.id, (profile,)))
    profiles = CustomProfilePresenter(_ProfileView(), config, repository)
    profiles.load()
    profiles.activate_selected()
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 999}', encoding="utf-8")

    with pytest.raises(ValueError):
        profiles.import_file(invalid)

    assert profiles.selected_profile == profile
    assert config.active_custom_profile == profile
    assert repository.load().selected_profile == profile


def test_load_atomically_selects_first_profile_when_document_has_no_selection(tmp_path: Path) -> None:
    config = ConfigPresenter(_ConfigView())
    config.load()
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    first = _profile("first")
    second = _profile("second")
    repository.save(CustomWorkflowProfileDocument(None, (first, second)))
    view = _ProfileView()
    profiles = CustomProfilePresenter(view, config, repository)

    loaded = profiles.load()

    assert loaded.selected_profile_id == first.id
    assert view.document.selected_profile_id == first.id
    assert repository.load().selected_profile_id == first.id


def test_invalid_internal_profile_file_degrades_to_empty_with_diagnostic(tmp_path: Path) -> None:
    config = ConfigPresenter(_ConfigView())
    config.load()
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    repository.path.write_text(
        '{"document_type":"transbridge.ai_workflow_profiles","schema_version":2,'
        '"selected_profile_id":null,"profiles":[]}',
        encoding="utf-8",
    )
    view = _ProfileView()
    profiles = CustomProfilePresenter(view, config, repository)

    assert profiles.load() == CustomWorkflowProfileDocument.empty()
    assert "unsupported schema_version" in view.error
    assert repository.path.read_text(encoding="utf-8").find('"schema_version":2') >= 0


def test_window_custom_entry_inherits_service_and_restores_builtin_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from transbridge.ui.tools.ai_translator import custom_profile_presenter as custom_module

    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    profile = _profile("低并发润色", max_concurrent=9)
    repository.save(CustomWorkflowProfileDocument(profile.id, (profile,)))
    monkeypatch.setattr(custom_module, "AiWorkflowProfileRepository", lambda: repository)
    global_config = LLMConfig(model="global-model", max_concurrent=3)
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: global_config.copy_for_execution())
    app = QApplication.instance() or QApplication([])
    ctx = SimpleNamespace(
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    step2 = SimpleNamespace(filtered_entries=lambda: [], locate_entry=lambda _entry_id: None)
    window = AITranslatorWindow(ctx, step2)
    controls = window._view.controls

    controls.mode_custom.click()
    assert not controls.custom_profile_group.isHidden()
    assert controls.custom_base_mode_combo.currentData() == "polish"
    assert controls.concurrent_spin.value() == 9
    assert controls.model_edit.text() == "global-model"
    assert window._view_port.mode == "polish"

    controls.mode_translate.click()
    assert controls.concurrent_spin.value() == 3
    assert window._config_presenter.active_custom_profile is None
    window.close()
    app.processEvents()


def test_window_disables_custom_start_when_no_profile_exists(tmp_path: Path, monkeypatch) -> None:
    from transbridge.ui.tools.ai_translator import custom_profile_presenter as custom_module

    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    monkeypatch.setattr(custom_module, "AiWorkflowProfileRepository", lambda: repository)
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    app = QApplication.instance() or QApplication([])
    ctx = SimpleNamespace(
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    step2 = SimpleNamespace(filtered_entries=lambda: [], locate_entry=lambda _entry_id: None)
    window = AITranslatorWindow(ctx, step2)

    window._view.controls.mode_custom.click()

    assert not window._view.controls.start_btn.isEnabled()
    assert "没有可用配置" in window._view.controls.preflight_label.full_text
    window.close()
    app.processEvents()


def test_invalid_internal_profile_file_keeps_builtin_window_modes_available(tmp_path: Path, monkeypatch) -> None:
    from transbridge.ui.tools.ai_translator import custom_profile_presenter as custom_module

    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    repository.path.write_text(
        '{"document_type":"transbridge.ai_workflow_profiles","schema_version":99,'
        '"selected_profile_id":null,"profiles":[]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(custom_module, "AiWorkflowProfileRepository", lambda: repository)
    monkeypatch.setattr(config_module.LLMConfig, "load_from_file", lambda: LLMConfig())
    app = QApplication.instance() or QApplication([])
    ctx = SimpleNamespace(
        collection=None,
        esp_path=None,
        current_project=None,
        label_library={},
        entry_labels={},
    )
    step2 = SimpleNamespace(filtered_entries=lambda: [], locate_entry=lambda _entry_id: None)

    window = AITranslatorWindow(ctx, step2)
    controls = window._view.controls
    controls.mode_polish.click()
    assert controls.start_btn.text() == "▶ 开始润色"
    controls.mode_translate.click()
    assert controls.start_btn.text() == "▶ 开始翻译"

    controls.mode_custom.click()
    assert not controls.start_btn.isEnabled()
    assert "schema_version" in controls.custom_profile_status_label.text()
    assert "请导入有效配置" in controls.custom_profile_status_label.text()
    window.close()
    app.processEvents()
