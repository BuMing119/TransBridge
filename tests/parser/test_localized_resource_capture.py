"""Capture the exact loose/archive templates once for localized plugin writes."""

from dataclasses import FrozenInstanceError
from io import BytesIO
from pathlib import Path

import pytest
from sse_bsa import bsa_archive

from tests.bsa_fixtures import write_bsa
from transbridge.parser.strings_file import PluginStringsLookup, SkyrimStringsWriter

SUFFIXES = (".strings", ".dlstrings", ".ilstrings")


def _payload(suffix, *, string_id=1, text="Source text"):
    return SkyrimStringsWriter.to_bytes(
        {string_id: text, 900 + string_id: "Unreferenced original"}, suffix != ".strings"
    )


def _plugin(tmp_path, name="Mod.esp"):
    path = tmp_path / name
    path.write_bytes(b"source path only; lookup does not read plugin")
    return path


def _loose(plugin, suffix, content, *, name=None, directory="Strings"):
    target = plugin.parent / directory / (name or f"{plugin.stem}_English{suffix}")
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(content)
    return target


def test_lookup_retains_all_original_loose_bytes_as_read_only_resources(tmp_path):
    plugin = _plugin(tmp_path)
    payloads = {suffix: _payload(suffix, string_id=index + 1) for index, suffix in enumerate(SUFFIXES)}
    paths = {suffix: _loose(plugin, suffix, content) for suffix, content in payloads.items()}

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup is not None and len(lookup) == 6
    assert isinstance(lookup.resources, tuple)
    assert [resource.suffix for resource in lookup.resources] == list(SUFFIXES)
    for resource in lookup.resources:
        assert resource.name == paths[resource.suffix].name
        assert resource.content == payloads[resource.suffix]
        assert resource.source_path == paths[resource.suffix]
        assert resource.member_path is None
        with pytest.raises(FrozenInstanceError):
            resource.content = b"changed"
    with pytest.raises(AttributeError):
        lookup.resources = ()


@pytest.mark.parametrize(
    "name,archive_name",
    [("Mod.esp", "Mod.bsa"), ("Skyrim.esm", "Skyrim - Interface.bsa"), ("_ResourcePack.esl", "_ResourcePack.bsa")],
)
def test_real_compressed_bsa_resources_retain_member_bytes_and_actual_origin(tmp_path, name, archive_name):
    plugin = _plugin(tmp_path, name)
    members = {
        f"strings/{plugin.stem.lower()}_english{suffix}": _payload(suffix, string_id=index + 1)
        for index, suffix in enumerate(SUFFIXES)
    }
    members["textures/unrelated.dds"] = b"Unrelated archive content must never become a resource"
    archive = write_bsa(tmp_path / archive_name, members)

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup is not None and len(lookup.resources) == 3
    assert {resource.suffix for resource in lookup.resources} == set(SUFFIXES)
    for resource in lookup.resources:
        assert resource.source_path == archive
        assert resource.member_path.replace("\\", "/") in members
        assert resource.content == members[resource.member_path.replace("\\", "/")]
        assert resource.name == Path(resource.member_path).name


def test_loose_files_override_only_their_own_type_and_bsa_fills_missing_types(tmp_path):
    plugin = _plugin(tmp_path)
    loose_bytes = _payload(".strings", text="Loose wins")
    loose = _loose(plugin, ".strings", loose_bytes)
    members = {
        "strings/mod_english.strings": _payload(".strings", text="Archive must not override loose"),
        "strings/mod_english.dlstrings": _payload(".dlstrings", string_id=2, text="BSA description"),
        "strings/mod_english.ilstrings": _payload(".ilstrings", string_id=3, text="BSA dialogue"),
    }
    archive = write_bsa(tmp_path / "Mod.bsa", members)

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert [lookup.get(string_id) for string_id in (1, 2, 3)] == ["Loose wins", "BSA description", "BSA dialogue"]
    resources = {resource.suffix: resource for resource in lookup.resources}
    assert resources[".strings"].source_path == loose
    assert resources[".strings"].content == loose_bytes
    assert all(resources[suffix].source_path == archive for suffix in SUFFIXES[1:])


def test_fully_loose_set_does_not_open_an_unneeded_broken_archive(tmp_path):
    plugin = _plugin(tmp_path)
    for index, suffix in enumerate(SUFFIXES):
        _loose(plugin, suffix, _payload(suffix, string_id=index + 1))
    (tmp_path / "Mod.bsa").write_bytes(b"not an archive")

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert len(lookup.resources) == 3
    assert all(resource.member_path is None for resource in lookup.resources)


def test_case_insensitive_loose_directory_and_basename_keep_actual_names(tmp_path):
    plugin = _plugin(tmp_path)
    path = _loose(plugin, ".strings", _payload(".strings"), name="MOD_ENGLISH.STRINGS", directory="sTrInGs")

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup.get(1) == "Source text"
    assert lookup.resources[0].name == path.name
    assert lookup.resources[0].source_path == path
    assert lookup.resources[0].suffix == ".strings"


def test_explicit_directory_api_preserves_resources_and_direct_dict_constructor_stays_compatible(tmp_path):
    plugin = _plugin(tmp_path)
    path = _loose(plugin, ".strings", _payload(".strings"))

    lookup = PluginStringsLookup.from_strings_dir(path.parent, plugin.stem)

    assert lookup.get(1) == "Source text"
    assert lookup.resources[0].content == path.read_bytes()
    assert PluginStringsLookup({7: "Existing constructor"}).get(7) == "Existing constructor"
    assert PluginStringsLookup({7: "Existing constructor"}).resources == ()
    assert PluginStringsLookup.from_strings_dir(tmp_path / "missing", "Unknown") is None


def test_input_candidates_include_all_missing_types_and_the_canonical_archive(tmp_path):
    from transbridge.parser.localized_resources import candidate_input_paths

    plugin = _plugin(tmp_path, "Update.esm")
    expected = tuple(tmp_path / "Strings" / f"Update_English{suffix}" for suffix in SUFFIXES)

    assert candidate_input_paths(plugin, "english") == (*expected, tmp_path / "Skyrim - Interface.bsa")
    resource_pack = _plugin(tmp_path, "_ResourcePack.esl")
    assert candidate_input_paths(resource_pack, "english")[-1] == tmp_path / "_ResourcePack.bsa"


def test_all_empty_tables_keep_old_none_semantics_but_raw_capture_retains_them(tmp_path):
    from transbridge.parser.localized_resources import capture_plugin_resources

    plugin = _plugin(tmp_path)
    for suffix in SUFFIXES:
        _loose(plugin, suffix, SkyrimStringsWriter.to_bytes({}, suffix != ".strings"))

    assert PluginStringsLookup.from_plugin(plugin) is None
    resources = capture_plugin_resources(plugin)
    assert len(resources) == 3
    assert all(resource.content == b"\x00" * 8 for resource in resources)


def test_valid_empty_loose_table_is_not_filled_from_bsa_and_is_kept_in_mixed_lookup(tmp_path):
    plugin = _plugin(tmp_path)
    empty = _loose(plugin, ".strings", b"\x00" * 8)
    write_bsa(
        tmp_path / "Mod.bsa",
        {
            "strings/mod_english.strings": _payload(".strings", text="Do not fill an existing empty table"),
            "strings/mod_english.dlstrings": _payload(".dlstrings", string_id=2),
        },
    )

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup.get(1) is None
    assert lookup.get(2) == "Source text"
    assert lookup.resources[0].content == b"\x00" * 8
    assert lookup.resources[0].source_path == empty


def test_archive_reads_only_missing_matching_types_and_closes_member_stream_and_archive(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    _loose(plugin, ".strings", _payload(".strings"))
    (tmp_path / "Mod.bsa").write_bytes(b"synthetic handle")
    payloads = {
        "Strings/MOD_ENGLISH.STRINGS": b"must not read loose-overridden member",
        "Strings/MOD_ENGLISH.DLSTRINGS": _payload(".dlstrings", string_id=2),
        "Strings/other_english.ilstrings": b"must not read another plugin",
        "textures/mod_english.ilstrings": b"must not read another directory",
        "Strings/mod_french.ilstrings": b"must not read another language",
    }
    reads, streams, closed = [], [], []

    class Archive:
        def __init__(self, path):
            assert path.name == "Mod.bsa"

        def glob(self, _pattern):
            return tuple(payloads)

        def get_file_stream(self, member):
            reads.append(member)
            stream = BytesIO(payloads[member])
            streams.append(stream)
            return stream

        def close(self):
            closed.append(True)

    monkeypatch.setattr(bsa_archive, "BSAArchive", Archive)

    lookup = PluginStringsLookup.from_plugin(plugin)

    assert lookup.get(2) == "Source text"
    assert reads == ["Strings/MOD_ENGLISH.DLSTRINGS"]
    assert all(stream.closed for stream in streams)
    assert closed == [True]


@pytest.mark.parametrize("origin", ["loose", "archive", "archive_member"])
def test_bad_resources_are_not_silently_treated_as_missing(tmp_path, origin, caplog):
    plugin = _plugin(tmp_path)
    if origin == "loose":
        source = _loose(plugin, ".strings", b"bad")
    elif origin == "archive":
        source = tmp_path / "Mod.bsa"
        source.write_bytes(b"bad")
    else:
        source = write_bsa(tmp_path / "Mod.bsa", {"strings/mod_english.strings": b"bad"})

    with pytest.raises(Exception) as failed:
        PluginStringsLookup.from_plugin(plugin)

    assert str(source) in str(failed.value)
    assert failed.value.__cause__ is not None
    assert source.name in caplog.text


def test_loose_lookup_parses_captured_bytes_without_a_second_physical_read(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    paths = tuple(
        _loose(plugin, suffix, _payload(suffix, string_id=index + 1)) for index, suffix in enumerate(SUFFIXES)
    )
    original_read = Path.read_bytes
    reads = []

    def read(path):
        if path in paths:
            reads.append(path)
        return original_read(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    lookup = PluginStringsLookup.from_plugin(plugin)
    before = tuple(resource.content for resource in lookup.resources)
    for path in paths:
        path.write_bytes(b"source changed after capture")

    assert reads == list(paths)
    assert tuple(resource.content for resource in lookup.resources) == before
    assert [lookup.get(index) for index in (1, 2, 3)] == ["Source text"] * 3


def test_actual_case_for_archive_path_and_member_names_is_preserved(tmp_path):
    from transbridge.parser.localized_resources import candidate_input_paths

    plugin = _plugin(tmp_path)
    member = "StRiNgS/MOD_ENGLISH.STRINGS"
    raw = _payload(".strings")
    archive = write_bsa(tmp_path / "MOD.BSA", {member: raw})

    lookup = PluginStringsLookup.from_plugin(plugin)

    resource = lookup.resources[0]
    assert candidate_input_paths(plugin)[-1].name == "MOD.BSA"
    assert resource.name == "MOD_ENGLISH.STRINGS"
    assert resource.member_path.replace("\\", "/") == member
    assert resource.source_path == archive
    assert resource.content == raw


def test_stream_read_failure_retains_cause_and_closes_both_owned_handles(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    (tmp_path / "Mod.bsa").write_bytes(b"synthetic handle")
    original = OSError("member read interrupted")
    streams, closed = [], []

    class FailedStream(BytesIO):
        def read(self, *_args):
            raise original

    class Archive:
        def __init__(self, _path):
            pass

        def glob(self, _pattern):
            return ("strings/mod_english.strings",)

        def get_file_stream(self, _member):
            stream = FailedStream()
            streams.append(stream)
            return stream

        def close(self):
            closed.append(True)

    monkeypatch.setattr(bsa_archive, "BSAArchive", Archive)

    with pytest.raises(ValueError, match="member read interrupted") as failed:
        PluginStringsLookup.from_plugin(plugin)

    assert failed.value.__cause__ is original
    assert streams[0].closed
    assert closed == [True]


def test_archive_with_no_matching_resource_still_closes_and_preserves_none_api(tmp_path, monkeypatch):
    plugin = _plugin(tmp_path)
    (tmp_path / "Mod.bsa").write_bytes(b"synthetic handle")
    closed = []

    class Archive:
        def __init__(self, _path):
            pass

        def glob(self, _pattern):
            return ()

        def close(self):
            closed.append(True)

    monkeypatch.setattr(bsa_archive, "BSAArchive", Archive)

    assert PluginStringsLookup.from_plugin(plugin) is None
    assert closed == [True]
