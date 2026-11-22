import sys

import pytest

from transbridge.config.paths import get_data_dir, translation_progress_root
from transbridge.config.storage_access import storage_access
from transbridge.config.storage_location import (
    StorageLocationError,
    active_storage_root,
    commit_storage_root,
    selected_storage_root,
)


def test_default_locations_remain_independent_until_user_selects_a_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert active_storage_root() is None
    assert get_data_dir() == str(tmp_path / "roaming" / "TransBridge" / "data")
    assert translation_progress_root() == tmp_path / "local" / "TransBridge" / "翻译进度"
    selected = selected_storage_root(tmp_path / "游戏工具")
    assert selected == tmp_path / "游戏工具" / "TransBridge"
    assert selected_storage_root(selected) == selected
    assert not selected.exists()
    assert not (tmp_path / "local").exists()


def test_unavailable_custom_drive_does_not_open_empty_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    selected = tmp_path / "drive" / "TransBridge"
    (selected / "data").mkdir(parents=True)
    (selected / "翻译进度").mkdir()
    commit_storage_root(selected)
    assert get_data_dir() == str(selected / "data")
    assert translation_progress_root() == selected / "翻译进度"
    selected.rename(tmp_path / "offline")
    with pytest.raises(StorageLocationError, match="重新连接"):
        get_data_dir()
    assert not selected.exists()
    (tmp_path / "offline").rename(selected)
    assert active_storage_root() == selected


def test_a_migration_requires_exclusive_access(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    with storage_access(), storage_access():
        with pytest.raises(StorageLocationError, match="正在使用"), storage_access(exclusive=True):
            pytest.fail("migration acquired storage still in use")
    with storage_access(exclusive=True):
        with pytest.raises(StorageLocationError, match="正在使用"), storage_access():
            pytest.fail("another runtime opened storage during migration")
