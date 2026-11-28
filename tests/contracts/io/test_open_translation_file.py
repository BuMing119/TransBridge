"""Opening files preserves native snapshots and applies optional translation policy."""

from dataclasses import replace
from functools import partial

import pytest

from tests.contracts.io.test_translation_import_policy import target_and_source
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import RequestContext
from transbridge.application.io.open_file_options import OpenFileOptions
from transbridge.application.io.open_translation_file import apply_open_file_dictionary, prepare_open_file
from transbridge.translation_memory.library import DictionaryLibrary
from transbridge.ui.source_hydration import collection_from_hydration

FORMATS = ("plugin.sse", "binary.eet", "xml.eet", "xml.xt", "sst.ssu8", "sst.ssu9", "json.dsd", "json.paratranz")


def test_open_patch_recovers_the_corresponding_master_record_after_hydration(tmp_path):
    master = write_plugin(tmp_path / "Base.esm", [(0x801, "First", "Light"), (0x802, "Second", "Light")])
    patch = write_plugin(tmp_path / "Patch.esp", [(0x802, "Second", "Light")], masters=("Base.esm",))
    request = RequestContext("test")
    opened = prepare_open_file(str(master), OpenFileOptions(), request)
    rows = collection_from_hydration(opened.source.hydration)
    library = DictionaryLibrary(tmp_path / "dictionaries")
    library.save(
        [replace(row, translation=text, stage=1) for row, text in zip(rows, ("甲", "乙"), strict=True)],
        master.stem,
    )
    result = prepare_open_file(
        str(patch),
        OpenFileOptions(),
        request,
        apply_dictionary=partial(apply_open_file_dictionary, library=library, mod_file_id=patch.stem),
    )
    assert [(row.translation, row.stage) for row in result.source.hydration.entries] == [("乙", 0)]


def source_file(tmp_path, kind):
    _target, path = target_and_source(tmp_path, kind)
    return path


@pytest.mark.parametrize("kind", FORMATS)
@pytest.mark.parametrize("explicit", [False, True])
def test_open_all_native_formats_by_content_and_explicit_hint(tmp_path, kind, explicit):
    path = source_file(tmp_path, kind)
    before = path.read_bytes()
    result = prepare_open_file(
        str(path), OpenFileOptions(format_hint=kind if explicit else None), RequestContext("test")
    )
    assert result.source.hydration.format_id == kind
    assert result.source.hydration.source_snapshot.content == before
    assert result.source.hydration.entries
    assert path.read_bytes() == before


@pytest.mark.parametrize("donor", FORMATS)
def test_open_plugin_with_optional_translation_uses_shared_import(tmp_path, donor):
    _target, translation = target_and_source(tmp_path, donor)
    plugin = tmp_path / "Test.esp"
    before = plugin.read_bytes()
    options = OpenFileOptions(translation_path=str(translation))
    opened = prepare_open_file(str(plugin), options, RequestContext("test"))
    assert opened.source.hydration.entries[0].translation == "你好"
    assert opened.source.hydration.source_snapshot.content == before
    assert plugin.read_bytes() == before


@pytest.mark.parametrize("overwrite", [False, True])
def test_open_eet_optional_translations_respect_existing_policy(tmp_path, overwrite):
    from transbridge.parser.eet_binary_codec import EetDocument

    path = source_file(tmp_path, "binary.eet")
    document = EetDocument.from_bytes(path.read_bytes())
    path.write_bytes(document.with_translation(0, "已有译文").to_bytes())
    _target, donor = target_and_source(tmp_path, "xml.eet")
    result = prepare_open_file(
        str(path), OpenFileOptions(translation_path=str(donor), replace_existing=overwrite), RequestContext("test")
    )
    assert result.source.hydration.entries[0].translation == ("你好" if overwrite else "已有译文")


@pytest.mark.parametrize("content", [b"[]", b"broken", b"{invalid"])
def test_ambiguous_and_corrupt_content_is_not_opened(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        prepare_open_file(str(path), OpenFileOptions(), RequestContext("test"))


def test_wrong_json_schema_is_not_silently_accepted(tmp_path):
    path = source_file(tmp_path, "json.paratranz")
    with pytest.raises(ValueError):
        prepare_open_file(str(path), OpenFileOptions(format_hint="json.dsd"), RequestContext("test"))


def test_damaged_optional_source_prevents_preparing_a_partial_open(tmp_path):
    path = write_plugin(tmp_path / "main.esp", [(0x800, "Name", "Hello")])
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"bad")
    with pytest.raises(ValueError):
        prepare_open_file(str(path), OpenFileOptions(translation_path=str(bad)), RequestContext("test"))


def test_open_rejects_source_changed_between_probe_and_parse(tmp_path, monkeypatch):
    from transbridge.application.io.use_case import TranslationIoUseCase

    path = source_file(tmp_path, "json.paratranz")
    parse = TranslationIoUseCase.parse

    def change_then_parse(self, request):
        path.write_bytes(path.read_bytes().replace(b"Hello", b"Changed"))
        return parse(self, request)

    monkeypatch.setattr(TranslationIoUseCase, "parse", change_then_parse)
    with pytest.raises(ValueError, match="已变化"):
        prepare_open_file(str(path), OpenFileOptions(), RequestContext("test"))


def test_empty_original_option_only_affects_plugin_parsing(tmp_path):
    path = write_plugin(tmp_path / "empty.esp", [(0x800, "Name", ""), (0x801, "Other", "Hello")])
    options = OpenFileOptions(skip_empty=True)
    filtered = prepare_open_file(str(path), options, RequestContext("test"))
    complete = prepare_open_file(str(path), replace(options, skip_empty=False), RequestContext("test"))
    assert len(filtered.source.hydration.entries) == 1
    assert len(complete.source.hydration.entries) == 2
