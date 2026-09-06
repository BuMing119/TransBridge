from copy import deepcopy
from types import SimpleNamespace

from transbridge.application.translation.custom_workflow_profile import CustomWorkflowProfile
from transbridge.config.ai_workflow_profiles import AiWorkflowProfileRepository
from transbridge.config.llm import LLMConfig
from transbridge.ui.tools.ai_translator.ai_translator_window import AITranslatorWindow
from transbridge.ui.tools.ai_translator.config_presenter import ConfigPresenter
from transbridge.ui.tools.ai_translator.custom_profile_presenter import CustomProfilePresenter


class View:
    def __init__(self):
        self.config = None

    def render_config(self, config):
        self.config = deepcopy(config)

    def update_config(self, config):
        return deepcopy(self.config)

    def render_profiles(self, document):
        self.document = document


def test_task_draft_changes_and_mode_switches_never_save_global_or_custom(monkeypatch):
    original = LLMConfig(model="original", api_key="private")
    baseline = deepcopy(original)
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: original)
    writes = []
    monkeypatch.setattr(LLMConfig, "save_to_file", lambda self: writes.append(self))
    view = View()
    presenter = ConfigPresenter(view, task_draft=True)
    presenter.load()
    view.config.max_concurrent = 9
    assert presenter.save().max_concurrent == 9
    presenter.switch_preset("polish")
    custom = CustomWorkflowProfile.from_config("Custom", "polish", original)
    presenter.activate_custom(custom, writes.append)
    view.config.max_concurrent = 13
    presenter.save()
    presenter.switch_preset("translate")
    assert writes == []
    assert original == baseline


def test_explicit_service_refresh_survives_custom_and_builtin_switches(monkeypatch):
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: LLMConfig(model="old"))
    presenter = ConfigPresenter(View(), task_draft=True)
    presenter.load()
    presenter.refresh_service(LLMConfig(model="new-service", api_key="new-key"))
    profile = CustomWorkflowProfile.from_config("Custom", "polish", LLMConfig())
    presenter.activate_custom(profile, lambda _: None)
    assert presenter.build().model == "new-service"
    presenter.switch_preset("translate")
    assert presenter.build().model == "new-service"


def test_custom_base_mode_change_is_only_a_task_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: LLMConfig())
    config = ConfigPresenter(View(), task_draft=True)
    config.load()
    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    profile = CustomWorkflowProfile.from_config("Custom", "polish", config.build())
    repository.upsert(profile, select=True)
    baseline = repository.path.read_bytes()
    presenter = CustomProfilePresenter(View(), config, repository)
    presenter.load()
    presenter.activate_selected()
    presenter.change_base_mode("mixed")
    assert config.active_custom_profile.base_mode == "mixed"
    assert repository.path.read_bytes() == baseline


def test_explicit_save_task_preset_persists_only_safe_workflow_fields(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog, QMessageBox

    repository = AiWorkflowProfileRepository(tmp_path / "profiles.json")
    monkeypatch.setattr("transbridge.config.ai_workflow_profiles.AiWorkflowProfileRepository", lambda: repository)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args: ("My task", True))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    config = LLMConfig(model="private-model", api_key="private-secret", local_json_path="private-path")
    window = SimpleNamespace(
        _view_port=SimpleNamespace(mode="mixed"), _config_presenter=SimpleNamespace(build=lambda: config)
    )
    AITranslatorWindow.on_save_task_preset(window)
    saved = repository.load().selected_profile
    assert saved.name == "My task" and saved.base_mode == "mixed"
    text = repository.path.read_text(encoding="utf-8")
    assert "private-secret" not in text and "private-path" not in text and "private-model" not in text


def test_mixed_keeps_common_scope_and_removed_source_disables_task_without_qt_exception(monkeypatch, tmp_path):
    from PyQt6.QtWidgets import QApplication

    from transbridge.converter.translation_entry import TranslationEntry
    from transbridge.converter.translation_entry_collection import TranslationEntryCollection
    from transbridge.ui.projection_types import CollectionSlot

    app = QApplication.instance() or QApplication([])
    original, repository = _repository_config(tmp_path)
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: original)
    before = repository.path.read_bytes()
    slot = CollectionSlot("Demo", TranslationEntryCollection([TranslationEntry("one", "one", "Source", "", 0, "")]))
    ctx = SimpleNamespace(
        slots={"demo": slot},
        active_slot=slot,
        collection=slot.collection,
        esp_path=None,
        current_project=None,
        entry_labels={},
        label_library={},
    )
    workbench = SimpleNamespace(filtered_entries=lambda: tuple(slot.collection), locate_entry=lambda _: None)
    window = AITranslatorWindow(ctx, workbench)
    window.show()
    window._view.controls.mode_mixed.click()
    app.processEvents()
    assert window._view._scope_filter_box.isVisible()
    assert window._view.controls.scope_stage_all_btn.menu().actions()
    ctx.slots = {}
    window.update_estimate()
    window.update_quick_run()
    assert not window._view.controls.start_btn.isEnabled()
    assert "来源已变化" in window._view.controls.preflight_label.full_text
    window.close()
    assert repository.path.read_bytes() == before


def _repository_config(tmp_path):
    from transbridge.config.paratranz_credentials import UnavailableCredentialStore
    from transbridge.config.repository import ConfigRepository

    path = tmp_path / "config.ini"
    repository = ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())
    LLMConfig(model="demo").save_to_file(repository=repository)
    return LLMConfig.load_from_file(repository=repository, environment={}), repository


def test_repository_backed_task_draft_detaches_locks_and_preserves_nested_values(monkeypatch, tmp_path):
    original, repository = _repository_config(tmp_path)
    before = repository.path.read_bytes()
    original_values = original.copy_for_execution()
    monkeypatch.setattr(LLMConfig, "load_from_file", lambda: original)
    view = View()
    presenter = ConfigPresenter(view, task_draft=True)

    loaded = presenter.load()
    assert loaded._repository is None
    assert loaded._credential_store is None
    assert loaded.config_revision == original.config_revision
    view.config.term_priority.reverse()
    view.config.embedding.model = "task-only-model"
    first = presenter.build()
    second = presenter.build()
    first.term_priority.clear()
    assert second.term_priority
    presenter.switch_preset("polish")
    profile = CustomWorkflowProfile.from_config("Task", "mixed", presenter.build())
    presenter.activate_custom(profile, lambda _: None)
    presenter.save()
    presenter.switch_preset("translate")

    assert original.copy_for_execution() == original_values
    assert repository.path.read_bytes() == before
