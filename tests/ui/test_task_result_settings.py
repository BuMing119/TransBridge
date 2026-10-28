from __future__ import annotations

from transbridge.application.translation.result_merge import ResultConflictPolicy as Policy
from transbridge.config.task_results import TaskResultPreferenceRepository, TaskResultPreferences
from transbridge.ui.settings.sections import SettingsSection

from . import test_ui_settings_dialog
from .test_ui_settings_dialog import _dialog, _preferences

qapp = test_ui_settings_dialog.qapp


def test_cancel_discards_conflict_default_edits(qapp, tmp_path):
    preferences = _preferences(tmp_path)
    results = TaskResultPreferenceRepository(preferences._repository)
    service, dialog = _dialog(qapp, preferences, task_result_preferences=results)
    page = dialog._settings_pages[SettingsSection.TASK_RESULTS]
    page.ai_policy.setCurrentIndex(page.ai_policy.findData(Policy.PREFER_INCOMING))
    dialog.reject()
    assert results.load() == TaskResultPreferences()
    service.close()


def test_apply_saves_both_default_choices(qapp, tmp_path):
    preferences = _preferences(tmp_path)
    results = TaskResultPreferenceRepository(preferences._repository)
    service, dialog = _dialog(qapp, preferences, task_result_preferences=results)
    page = dialog._settings_pages[SettingsSection.TASK_RESULTS]
    page.ai_policy.setCurrentIndex(page.ai_policy.findData(Policy.PREFER_INCOMING))
    page.download_policy.setCurrentIndex(page.download_policy.findData(Policy.DEFER))
    assert dialog._apply_button.isEnabled()
    assert dialog.apply_draft()
    assert results.load() == TaskResultPreferences(Policy.PREFER_INCOMING, Policy.DEFER)
    service.close()


def test_failed_preference_save_keeps_dialog_and_draft(qapp, tmp_path, monkeypatch):
    preferences = _preferences(tmp_path)
    results = TaskResultPreferenceRepository(preferences._repository)
    service, dialog = _dialog(qapp, preferences, task_result_preferences=results)
    page = dialog._settings_pages[SettingsSection.TASK_RESULTS]
    page.ai_policy.setCurrentIndex(page.ai_policy.findData(Policy.PREFER_INCOMING))

    def fail(_preferences):
        raise OSError("disk full")

    monkeypatch.setattr(results, "save", fail)
    assert not dialog.apply_draft()
    assert "disk full" in dialog.last_notice
    assert page.ai_policy.currentData() == Policy.PREFER_INCOMING
    assert results.load() == TaskResultPreferences()
    dialog.reject()
    service.close()
