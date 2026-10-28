from __future__ import annotations

import pytest

from transbridge.application.translation.result_merge import ResultConflictPolicy as Policy
from transbridge.config.paratranz_credentials import UnavailableCredentialStore
from transbridge.config.repository import ConfigRepository
from transbridge.config.task_results import TaskResultPreferenceRepository, TaskResultPreferences


def repository(tmp_path):
    path = tmp_path / "settings.ini"
    return ConfigRepository(path, legacy_path=path, credential_store=UnavailableCredentialStore())


def test_defaults_and_invalid_values_keep_current(tmp_path):
    config = repository(tmp_path)
    preferences = TaskResultPreferenceRepository(config)
    assert preferences.load() == TaskResultPreferences()
    config.update_sections({"task_results": {"ai_policy": "invalid", "download_policy": "defer"}})
    assert preferences.load() == TaskResultPreferences(Policy.KEEP_CURRENT, Policy.DEFER)


def test_preferences_roundtrip_preserves_unrelated_changes_and_frozen_value(tmp_path):
    config = repository(tmp_path)
    preferences = TaskResultPreferenceRepository(config)
    frozen = preferences.load()
    config.update_sections({"other": {"value": "new"}})
    preferences.save(TaskResultPreferences(Policy.PREFER_INCOMING, Policy.DEFER))
    assert TaskResultPreferenceRepository(repository(tmp_path)).load() == TaskResultPreferences(
        Policy.PREFER_INCOMING, Policy.DEFER
    )
    assert frozen == TaskResultPreferences()
    assert config.load().value("other", "value") == "new"


def test_failed_write_keeps_previous_preferences(tmp_path, monkeypatch):
    config = repository(tmp_path)
    preferences = TaskResultPreferenceRepository(config)
    preferences.save(TaskResultPreferences())

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(config, "_replace", fail)
    with pytest.raises(Exception, match="disk full"):
        preferences.save(TaskResultPreferences(Policy.PREFER_INCOMING))
    assert preferences.load() == TaskResultPreferences()
