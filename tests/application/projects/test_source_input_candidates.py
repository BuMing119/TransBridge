from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from transbridge.application.projects import source_input_candidates as candidates
from transbridge.application.projects.source_input_candidates import (
    InputNeed,
    collect_input_files,
    match_input_candidates,
)


def test_explicit_selections_are_kept_without_filtering_or_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Explicit files and saved reference paths must not be inspected")

    monkeypatch.setattr(Path, "lstat", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    paths = ("missing/source.custom", "missing/source.custom", "unavailable.xml")
    result = collect_input_files(paths)
    assert result.paths == paths
    assert result.errors == ()


def test_directory_discovery_is_recursive_case_insensitive_and_metadata_only(tmp_path, monkeypatch):
    nested = tmp_path / "nested"
    nested.mkdir()
    names = ("plugin.ESP", "master.esm", "light.esl", "binary.eet", "text.xml", "data.json", "translation.sst")
    names += ("one.strings", "two.dlstrings", "three.ilstrings")
    for name in names:
        (nested / name).touch()
    (nested / "ignore.zip").touch()
    (tmp_path / "ignore.txt").touch()
    (tmp_path / "directory.xml").mkdir()

    def forbidden(*args, **kwargs):
        raise AssertionError("Candidate discovery must not open content")

    monkeypatch.setattr(Path, "open", forbidden)
    result = collect_input_files(("explicit.custom",), directory=tmp_path)
    assert result.paths[0] == "explicit.custom"
    assert set(result.paths[1:]) == {str(nested / name) for name in names}
    assert result.errors == ()


def test_directory_discovery_reports_access_error_and_keeps_other_results(tmp_path, monkeypatch):
    denied = tmp_path / "denied"
    denied.mkdir()
    good = tmp_path / "good.xml"
    good.touch()
    scan = candidates.os.scandir

    def scan_with_error(path):
        if Path(path) == denied:
            raise PermissionError("Access denied")
        return scan(path)

    monkeypatch.setattr(candidates.os, "scandir", scan_with_error)
    result = collect_input_files(directory=tmp_path)
    assert result.paths == (str(good),)
    assert len(result.errors) == 1
    assert str(denied) in result.errors[0]
    assert "Access denied" in result.errors[0]


def test_directory_discovery_reports_missing_directory(tmp_path):
    missing = tmp_path / "gone"
    result = collect_input_files(("explicit.xml",), directory=missing)
    assert result.paths == ("explicit.xml",)
    assert len(result.errors) == 1
    assert str(missing) in result.errors[0]


def test_directory_discovery_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.xml").touch()
    selected = tmp_path / "selected"
    selected.mkdir()
    link = selected / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    assert collect_input_files(directory=selected).paths == ()
    assert collect_input_files(directory=link).paths == ()


def test_directory_discovery_does_not_scan_selected_junction(tmp_path, monkeypatch):
    def junction_metadata(path):
        return SimpleNamespace(st_mode=stat.S_IFDIR, st_reparse_tag=stat.IO_REPARSE_TAG_MOUNT_POINT)

    def forbidden(*args):
        raise AssertionError("Directory junction must not be enumerated")

    monkeypatch.setattr(Path, "lstat", junction_metadata)
    monkeypatch.setattr(candidates.os, "scandir", forbidden)
    assert collect_input_files(directory=tmp_path).paths == ()


def test_matching_keeps_ambiguous_paths_and_never_assigns_or_inspects_references(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Name matching must not inspect files")

    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    paths = ("/first/Plugin.XML", "/second/plugin.xml", "/third/other.xml", "/fourth/plugin.xml.backup")
    needs = (
        InputNeed("label", "plugin.xml", 12, False),
        InputNeed("windows", "Saved source", 7, False, r"Z:\unavailable\PLUGIN.xml"),
        InputNeed("posix", "Another source", 8, False, "/unavailable/other.xml"),
        InputNeed("ready", "plugin.xml", 9, True),
        InputNeed("missing", "absent.xml", 2, False),
    )
    assert match_input_candidates(needs, paths) == {
        "label": paths[:2],
        "windows": paths[:2],
        "posix": (paths[2],),
        "missing": (),
    }


def test_matching_combines_label_and_reference_and_handles_windows_candidates():
    paths = (r"D:\chosen\label.xml", r"E:\chosen\reference.xml", r"F:\chosen\unrelated.xml")
    needs = (InputNeed("source", "label.xml", 10, False, r"C:\old\reference.xml"),)
    assert match_input_candidates(needs, paths) == {"source": paths[:2]}
