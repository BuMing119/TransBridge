import pytest

from transbridge.config.dictionary_preferences import DictionaryPreferences
from transbridge.config.repository import ConfigRepository


def test_switches_persist_independently_in_existing_configuration(tmp_path):
    path = tmp_path / "settings.ini"
    repo = ConfigRepository(path, legacy_path=path)
    repo.update_sections({"ui": {"locale": "en-US"}})
    prefs = DictionaryPreferences(repo)
    assert prefs.save_on_project_save and prefs.apply_on_plugin_open
    prefs.set_save_on_project_save(False)
    reopened = DictionaryPreferences(ConfigRepository(path, legacy_path=path))
    assert not reopened.save_on_project_save and reopened.apply_on_plugin_open
    reopened.set_apply_on_plugin_open(False)
    prefs.set_save_on_project_save(True)
    assert prefs.save_on_project_save and not prefs.apply_on_plugin_open
    assert repo.load().value("ui", "locale") == "en-US"


def test_failed_write_preserves_last_saved_choice(tmp_path):
    def reject_replace(*args):
        raise OSError("test write failure")

    path = tmp_path / "settings.ini"
    prefs = DictionaryPreferences(ConfigRepository(path, legacy_path=path, replace_func=reject_replace))
    assert prefs.apply_on_plugin_open
    with pytest.raises(OSError, match="test write failure"):
        prefs.set_apply_on_plugin_open(False)
    assert prefs.apply_on_plugin_open and not path.exists()


def test_pending_changes_are_immediate_and_cancel_restores_only_that_key(tmp_path):
    path = tmp_path / "settings.ini"
    prefs = DictionaryPreferences(ConfigRepository(path, legacy_path=path))
    staged_save = prefs.stage_set("save_on_project_save", False)
    staged_apply = prefs.stage_set("apply_on_plugin_open", False)
    assert not prefs.save_on_project_save and not prefs.apply_on_plugin_open
    staged_apply.commit()
    staged_save.cancel()
    staged_save.commit()
    assert prefs.save_on_project_save and not prefs.apply_on_plugin_open
    restored = DictionaryPreferences(ConfigRepository(path, legacy_path=path))
    assert restored.save_on_project_save and not restored.apply_on_plugin_open
