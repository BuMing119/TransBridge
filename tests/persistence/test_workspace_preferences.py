"""Retired project metadata stays opaque while UI preferences round-trip."""

import json

from transbridge.persistence.workspace import WorkspaceState


def test_old_workspace_preferences_preserve_original_project_metadata(tmp_path):
    path = tmp_path / "workspace.json"
    original = {
        "version": 1,
        "projects": {"Old": "old/project.json"},
        "active_project": "Old",
        "settings": {"auto_save_interval_minutes": 9},
        "last_session": {"project": "Old", "filter_state": {"stage": [3]}},
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    preferences = WorkspaceState.load(path)
    assert preferences.settings["auto_save_interval_minutes"] == 9
    assert preferences.last_session["filter_state"] == {"stage": [3]}
    assert not hasattr(preferences, "add_project")
    assert not hasattr(preferences, "active_project")
    preferences.last_session["filter_state"] = {"stage": [5]}
    preferences.save()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["projects"] == original["projects"]
    assert stored["active_project"] == "Old"
    assert stored["last_session"]["filter_state"] == {"stage": [5]}
