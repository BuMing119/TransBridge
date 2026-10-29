from pathlib import Path

from transbridge.application.preflight.sources import existing_reference, source_reference


def test_reference_check_never_reads_contents_and_only_clears_missing_paths(tmp_path, monkeypatch):
    present = tmp_path / "existing.esp"
    present.write_bytes(b"not a plugin and deliberately not parsed")
    monkeypatch.setattr(Path, "read_bytes", lambda *_: (_ for _ in ()).throw(AssertionError("unexpected read")))
    assert existing_reference(str(present)) == str(present)
    assert existing_reference(str(tmp_path / "missing.esp")) == ""
    assert existing_reference(None) == ""


def test_reference_cleanup_keeps_identity_content_and_filename(tmp_path):
    old = str(tmp_path / "Original.esp")
    source = {"source_id": "stable", "location": old, "source_artifact_id": "saved", "legacy": {"path": old}}
    cleaned = source_reference(source)
    assert cleaned == {
        "source_id": "stable",
        "location": "",
        "display_name": "Original.esp",
        "source_artifact_id": "saved",
        "legacy": {"path": ""},
    }
    assert source["location"] == source["legacy"]["path"] == old


def test_unknown_existence_does_not_add_a_permission_gate(monkeypatch):
    def unavailable(_path):
        raise PermissionError("unavailable device")

    monkeypatch.setattr(Path, "stat", unavailable)
    assert existing_reference("remote.esp") == "remote.esp"
