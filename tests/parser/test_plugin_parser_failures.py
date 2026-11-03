import pytest

from transbridge.parser.plugin_parser import PluginParser


def test_strict_parse_preserves_loading_exception_and_default_compatibility(tmp_path, monkeypatch):
    path = tmp_path / "test.esp"
    path.write_bytes(b"TES4" + bytes(20))
    original = OSError("plugin cannot be read")

    def fail(_path):
        raise original

    monkeypatch.setattr("transbridge.parser.plugin_parser.SSEPluginWithContext.from_file", fail)
    parser = PluginParser()
    parser._plugin = object()
    parser._strings_lookup = object()
    with pytest.raises(OSError) as caught:
        parser.parse_plugin(path, raise_on_error=True)
    assert caught.value is original
    assert parser.get_plugin() is None
    assert parser.get_strings_lookup() is None
    assert parser.parse_plugin(path) == []


def test_strict_parse_accepts_valid_plugin_without_translatable_strings(tmp_path, monkeypatch):
    from types import SimpleNamespace

    path = tmp_path / "test.esp"
    path.write_bytes(b"TES4" + bytes(20))
    plugin = SimpleNamespace(extract_strings_with_context=lambda **_kwargs: [], require_resolved_strings=lambda: None)
    monkeypatch.setattr("transbridge.parser.plugin_parser.SSEPluginWithContext.from_file", lambda _path: plugin)
    parser = PluginParser()
    assert parser.parse_plugin(path, raise_on_error=True, discover_sibling_strings=False) == []
    assert parser.get_plugin() is plugin
