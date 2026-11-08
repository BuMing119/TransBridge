"""Single-file import policies preserve identities and protected translations."""

from dataclasses import replace
import json
import struct

import pytest

from tests.plugin_fixtures import write_plugin
from tests.source_format_fixtures import write_translation_source
from transbridge.application.io.migration_import import MigrationImportError
from transbridge.application.io.translation_import import prepare_translation_import
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.plugin_parser import PluginParser


def target_and_source(tmp_path, kind, translation="旧译文", stage=3):
    plugin = write_plugin(tmp_path / "Test.esp", [(0x800, "Greeting", "Hello")])
    entry = PluginParser().parse_plugin(plugin)[0]
    target = TranslationEntryCollection([replace(entry, translation=translation, stage=stage)])
    if kind in {"binary.eet", "sst.ssu8", "sst.ssu9", "json.dsd"}:
        source = write_translation_source(tmp_path, kind)
    elif kind == "json.paratranz":
        source = tmp_path / "translated.json"
        source.write_text(json.dumps([{"key": entry.key, "original": "Hello", "translation": "你好", "stage": 1}]))
    elif kind == "plugin.sse":
        source = write_plugin(tmp_path / "translated.esp", [(0x800, "Greeting", "你好")])
    else:
        source = tmp_path / "translated.xml"
        source.write_text(
            "<DocumentElement><ESP><GRUP>NPC_</GRUP><ID>00000800</ID><EDID>Greeting</EDID>"
            "<CHAMP>FULL</CHAMP><ORIGINAL>Hello</ORIGINAL><TRADUIT>你好</TRADUIT>"
            "<INDEX>1</INDEX><STATUS>99</STATUS></ESP></DocumentElement>"
            if kind == "xml.eet"
            else "<SSTXMLRessources><Content><String><EDID>Greeting</EDID><REC>NPC_:FULL</REC>"
            "<Source>Hello</Source><Dest>你好</Dest></String></Content></SSTXMLRessources>",
            encoding="utf-8",
        )
    return target, source


@pytest.mark.parametrize(
    "kind", ["binary.eet", "sst.ssu8", "sst.ssu9", "json.dsd", "json.paratranz", "xml.eet", "xml.xt", "plugin.sse"]
)
@pytest.mark.parametrize("replace_existing", [False, True])
def test_existing_translation_policy_applies_to_each_independent_format(tmp_path, kind, replace_existing):
    target, source = target_and_source(tmp_path, kind)
    before = tuple(entry.snapshot() for entry in target)
    draft = prepare_translation_import(str(source), target, replace_existing=replace_existing)
    actual = next(iter(draft.collection))
    assert actual.translation == ("你好" if replace_existing else "旧译文")
    assert actual.identity == next(iter(target)).identity
    assert actual.metadata == next(iter(target)).metadata
    assert tuple(entry.snapshot() for entry in target) == before


@pytest.mark.parametrize("kind", ["binary.eet", "json.paratranz", "xml.eet", "xml.xt"])
@pytest.mark.parametrize("stage", [-1, 9])
def test_replace_preserves_locked_and_hidden_entries(tmp_path, kind, stage):
    target, source = target_and_source(tmp_path, kind, stage=stage)
    draft = prepare_translation_import(str(source), target, replace_existing=True)
    assert tuple(entry.snapshot() for entry in draft.collection) == tuple(entry.snapshot() for entry in target)


def test_default_fills_empty_and_never_adds_source_entries(tmp_path):
    target, source = target_and_source(tmp_path, "json.paratranz", "", 0)
    payload = json.loads(source.read_text())
    payload.append({"key": "unmatched", "original": "Other", "translation": "其他"})
    source.write_text(json.dumps(payload))
    draft = prepare_translation_import(str(source), target)
    assert len(draft.collection) == 1
    assert next(iter(draft.collection)).translation == "你好"


def test_json_conflicting_explicit_format_fails_without_mutation(tmp_path):
    target, source = target_and_source(tmp_path, "json.paratranz")
    with pytest.raises(MigrationImportError, match="IMPORT_FORMAT_AMBIGUOUS"):
        prepare_translation_import(str(source), target, format_hint="json.dsd")
    assert next(iter(target)).translation == "旧译文"


def test_repeated_xml_import_can_replace_previous_translation(tmp_path):
    target, source = target_and_source(tmp_path, "xml.eet", "", 0)
    first = prepare_translation_import(str(source), target)
    source.write_text(source.read_text(encoding="utf-8").replace("你好", "您好"), encoding="utf-8")
    second = prepare_translation_import(str(source), first.collection, replace_existing=True)
    assert next(iter(second.collection)).translation == "您好"


def test_strings_replace_preserves_locked_entries(tmp_path):
    target, _source = target_and_source(tmp_path, "json.paratranz")
    entry = replace(next(iter(target)), string_id=7)
    target = TranslationEntryCollection([entry])
    text = "你好".encode() + b"\0"
    (tmp_path / "Test_Chinese.strings").write_bytes(struct.pack("<IIII", 1, len(text), 7, 0) + text)
    draft = prepare_translation_import(
        str(tmp_path), target, format_hint="strings-directory", replace_existing=True, plugin_stem="Test"
    )
    assert next(iter(draft.collection)).translation == "你好"
    assert draft.strings_lookup.get(7) == "你好"


@pytest.mark.parametrize("kind", ["xml.eet", "xml.xt"])
def test_xml_import_into_binary_eet_keeps_physical_identity(tmp_path, kind):
    from tests.eet_fixtures import eet_record
    from transbridge.converter.eet_entries import document_entries
    from transbridge.parser.eet_binary_codec import EetDocument

    _plugin, source = target_and_source(tmp_path, kind)
    target = TranslationEntryCollection(
        document_entries(EetDocument(3, "Skyrim SE", (eet_record(translation="旧译文"),), None, 1))
    )
    identity = next(iter(target)).identity
    draft = prepare_translation_import(str(source), target, replace_existing=True)
    assert next(iter(draft.collection)).translation == "你好"
    assert next(iter(draft.collection)).identity == identity


@pytest.mark.parametrize("kind", ["xml.eet", "xml.xt"])
def test_duplicate_xml_source_is_not_chosen_arbitrarily(tmp_path, kind):
    from copy import deepcopy
    import xml.etree.ElementTree as ET

    target, source = target_and_source(tmp_path, kind)
    root = ET.fromstring(source.read_bytes())
    parent = root if kind == "xml.eet" else root.find("Content")
    parent.append(deepcopy(list(parent)[0]))
    source.write_bytes(ET.tostring(root))
    draft = prepare_translation_import(str(source), target, replace_existing=True)
    assert next(iter(draft.collection)).translation == "旧译文"


def test_dsd_plugin_name_mismatch_never_matches(tmp_path):
    target, source = target_and_source(tmp_path, "json.dsd")
    source.write_text(source.read_text().replace("Test.esp", "Other.esp"))
    with pytest.raises(MigrationImportError, match="MIGRATION_NO_PROVABLE_MATCHES"):
        prepare_translation_import(str(source), target, replace_existing=True)


@pytest.mark.parametrize("kind", ["xml.eet", "xml.xt"])
@pytest.mark.parametrize("editor_id", ["", "OtherNpc"])
def test_xml_with_unproved_editor_id_never_overwrites_known_target(tmp_path, kind, editor_id):
    target, source = target_and_source(tmp_path, kind)
    source.write_text(source.read_text(encoding="utf-8").replace("Greeting", editor_id), encoding="utf-8")
    draft = prepare_translation_import(str(source), target, replace_existing=True)
    # ESP-ESM XML still provides the exact FormID/field/index/original when its EDID is absent.
    expected = "你好" if kind == "xml.eet" and not editor_id else "旧译文"
    assert next(iter(draft.collection)).translation == expected


def test_dsd_unknown_plugin_never_falls_back_to_legacy_key(tmp_path):
    target, source = target_and_source(tmp_path, "json.dsd")
    source.write_text(source.read_text().replace("|Test.esp", ""))
    with pytest.raises(MigrationImportError, match="MIGRATION_DSD_LOCATOR_INVALID"):
        prepare_translation_import(str(source), target, replace_existing=True)


def test_shared_dsd_projection_rejects_tampered_snapshot_before_parsing(monkeypatch):
    from transbridge.application.io import dsd_projection
    from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot

    snapshot = SourceSnapshot.from_bytes(SourceDescriptor("Test.esp"), FormatId.PLUGIN_SSE, b"TES4original")
    object.__setattr__(snapshot, "content", b"TES4tampered")
    monkeypatch.setattr(
        dsd_projection, "target_entries_from_snapshot", lambda _snapshot: pytest.fail("parsed corrupt snapshot")
    )
    with pytest.raises(ValueError, match="快照内容与摘要不一致"):
        dsd_projection.restore_plugin_dsd_locators([], snapshot)
