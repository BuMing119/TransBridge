from pathlib import Path

import pytest

from tests.plugin_fixtures import write_plugin
from transbridge.parser.plugin_parser import PluginParser


def test_captured_plugin_bytes_are_authoritative_after_live_source_changes(tmp_path, monkeypatch):
    path = write_plugin(tmp_path / "fixture.esp", [(0x800, "Old", "Captured text")])
    captured = path.read_bytes()
    write_plugin(path, [(0x900, "New", "Changed live text")])

    def reject_open(*_args, **_kwargs):
        pytest.fail("The captured plugin source must not be reopened")

    monkeypatch.setattr(Path, "open", reject_open)
    entries = PluginParser().parse_plugin(
        path, captured_content=captured, raise_on_error=True, discover_sibling_strings=False
    )
    assert [entry.original for entry in entries] == ["Captured text"]
    assert "00000800" in entries[0].key


def test_capture_uses_header_bytes_for_discovery_without_reading_plugin_path(tmp_path, monkeypatch):
    path = write_plugin(tmp_path / "fixture.esp", [(0x800, "Npc", "Name")])
    captured = path.read_bytes()
    path.unlink()

    def reject_open(*_args, **_kwargs):
        pytest.fail("Discovery must inspect the captured header")

    monkeypatch.setattr(Path, "open", reject_open)
    entries = PluginParser().parse_plugin(path, captured_content=captured, raise_on_error=True)
    assert [entry.original for entry in entries] == ["Name"]


@pytest.mark.parametrize("content", [b"", b"TES4", b"WRNG" + bytes(20), b"TES4\xff\xff\xff\xff" + bytes(16)])
def test_strict_captured_content_validates_its_own_header(content):
    with pytest.raises(ValueError, match="TES4"):
        PluginParser().parse_plugin(Path("absent.esp"), captured_content=content, raise_on_error=True)


def test_default_error_policy_is_preserved_for_bad_captured_bytes(monkeypatch):
    def reject(*_args, **_kwargs):
        raise ValueError("Corrupt captured plugin")

    monkeypatch.setattr("transbridge.parser.plugin_parser.SSEPluginWithContext.from_stream", reject)
    parser = PluginParser()
    assert parser.parse_plugin(Path("absent.esp"), captured_content=b"broken") == []
    assert parser.get_plugin() is None
