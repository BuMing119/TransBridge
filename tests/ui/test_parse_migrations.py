"""Parse-time migration uses real sources and authoritative project hydration."""

from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from tests.plugin_fixtures import write_plugin
from tests.ui import test_parse_hydration_authority as parse_fixtures

project = parse_fixtures.project
parsing = parse_fixtures.parsing


def _config(**values):
    return SimpleNamespace(**{
        "esp_paths": [],
        "eet_path": None,
        "xt_path": None,
        "tp_path": None,
        "strings_dir": None,
        "strings_lang": "chinese",
        "skip_empty": True,
        **values,
    })


def _xml(path, kind):
    records = []
    for index in (1, 2):
        records.append(
            f"<ESP><GRUP>NPC_</GRUP><ID>{0x800 + index:08X}</ID><EDID>Npc{index}</EDID>"
            f"<CHAMP>FULL</CHAMP><ORIGINAL>Source {index}</ORIGINAL><TRADUIT>Target {index}</TRADUIT>"
            "<INDEX>1</INDEX><STATUS>99</STATUS></ESP>"
            if kind == "eet"
            else f"<String><EDID>Npc{index}</EDID><REC>NPC_:FULL</REC>"
            f"<Source>Source {index}</Source><Dest>Target {index}</Dest></String>"
        )
    content = (
        "<DocumentElement>" + "".join(records) + "</DocumentElement>"
        if kind == "eet"
        else "<SSTXMLRessources><Content>" + "".join(records) + "</Content></SSTXMLRessources>"
    )
    path.write_text(content, encoding="utf-8")
    return str(path)


def _strings(path, index, text):
    payload = text.encode("utf-8") + b"\0"
    path.write_bytes(struct.pack("<IIII", 1, len(payload), index, 0) + payload)


def _localized_plugin(path, index):
    def field(kind, value):
        return struct.pack("<4sH", kind, len(value)) + value

    def record(kind, form_id, value, flags=0):
        return struct.pack("<4sIIIIHH", kind, len(value), flags, form_id, 0, 44, 0) + value

    npc = record(
        b"NPC_",
        0x800 + index,
        field(b"EDID", f"Npc{index}".encode() + b"\0") + field(b"FULL", struct.pack("<I", index)),
    )
    path.write_bytes(
        record(b"TES4", 0, b"", flags=0x80)
        + struct.pack("<4sI4sIHHHH", b"GRUP", len(npc) + 24, b"NPC_", 0, 0, 0, 0, 0)
        + npc
    )


def _inputs(root, kind, batch):
    paths = [root / f"source-{index}.esp" for index in range(1, 3 if batch else 2)]
    for index, path in enumerate(paths, 1):
        write_plugin(path, [(0x800 + index, f"Npc{index}", f"Source {index}")])
    cfg = _config(esp_paths=[str(path) for path in paths])
    if kind in ("eet", "xt"):
        setattr(cfg, f"{kind}_path", _xml(root / f"translated-{kind}.xml", kind))
    elif kind == "tp":
        cfg.tp_path = str(
            write_plugin(root / "translated.esp", [(0x800 + i, f"Npc{i}", f"Target {i}") for i in (1, 2)])
        )
    else:
        original_dir, translated_dir = root / "Strings", root / "translated-strings"
        original_dir.mkdir()
        translated_dir.mkdir()
        for index, path in enumerate(paths, 1):
            _localized_plugin(path, index)
            _strings(original_dir / f"{path.stem}_English.strings", index, f"Source {index}")
            _strings(translated_dir / f"{path.stem}_Chinese.strings", index, f"Target {index}")
        cfg.strings_dir = str(translated_dir)
    return cfg


def _run(project, parsing, cfg, kind):
    getattr(parsing.coordinator, f"_run_{kind}")(cfg)
    parse_fixtures._until(project, lambda: not parsing.state["parsing"])


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
@pytest.mark.parametrize("source", ["eet", "xt", "tp", "strings"])
def test_selected_migrations_reach_every_parsed_source_and_authority(project, parsing, source, batch):
    cfg = _inputs(project.root, source, batch)
    _run(project, parsing, cfg, "batch_parse_esp" if batch else "parse_esp")

    keys = parse_fixtures.source_keys(project, cfg.esp_paths)
    assert list(project.ctx.slots) == keys, parsing.messages
    for index, key in enumerate(keys, 1):
        slot = project.ctx.slots[key]
        assert [entry.translation for entry in slot.collection] == [f"Target {index}"]
        assert slot.migrate_count == 1
        if source != "tp":
            field = "strings_path" if source == "strings" else f"{source}_path"
            assert getattr(slot, field) == getattr(cfg, "strings_dir" if source == "strings" else field)
    assert not project.ctx.authoritative_projection_diverged()
    assert not any("失败" in message for message in parsing.messages)
    assert project.ctx.project_commands.save(project.request).is_success


@pytest.mark.parametrize("source", ["eet", "xt", "tp", "strings"])
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_invalid_migration_source_is_reported_and_retry_targets_existing_collection(
    project, parsing, monkeypatch, source, batch
):
    cfg = _inputs(project.root, source, batch)
    field = "strings_dir" if source == "strings" else f"{source}_path"
    selected = Path(getattr(cfg, field))
    if source == "strings":
        # A readable first file must not leak partial translations when the
        # following file from the same selected source is corrupt.
        for file in list(selected.iterdir()):
            file.with_suffix(".dlstrings").write_bytes(b"invalid strings")
    else:
        selected.write_text("<broken", encoding="utf-8")
    notices = []
    parsing.host.show_message = lambda message, **options: notices.append((message, options))
    opened = []
    monkeypatch.setattr(parsing.coordinator, "apply_migration", lambda: opened.append(project.ctx.active_slot))

    _run(project, parsing, cfg, "batch_parse_esp" if batch else "parse_esp")

    keys = parse_fixtures.source_keys(project, cfg.esp_paths)
    assert list(project.ctx.slots) == keys
    first = project.ctx.slots[keys[0]]
    assert all(not entry.translation for slot in project.ctx.slots.values() for entry in slot.collection)
    assert not project.ctx.authoritative_projection_diverged()
    if source != "tp":
        assert getattr(first, "strings_path" if source == "strings" else field) is None
    message, options = notices[-1]
    assert "迁移部分失败" in message and str(selected) in message
    assert options["error"] is True and not opened
    options["retry"]()
    assert opened == [first]
    project.ctx.remove_slot(keys[0])
    options["retry"]()
    assert opened == [first]
    assert "原集合已变化" in notices[-1][0]


def test_successful_migration_survives_another_selected_source_failure(project, parsing):
    cfg = _inputs(project.root, "eet", True)
    bad = project.root / "broken-xt.xml"
    bad.write_text("<broken", encoding="utf-8")
    cfg.xt_path = str(bad)
    _run(project, parsing, cfg, "batch_parse_esp")

    assert [next(iter(slot.collection)).translation for slot in project.ctx.slots.values()] == ["Target 1", "Target 2"]
    assert all(slot.eet_path == cfg.eet_path and slot.xt_path is None for slot in project.ctx.slots.values())
    assert not project.ctx.authoritative_projection_diverged()
    assert "2 个集合的迁移部分失败" in parsing.messages[-1]
    assert "XTranslator XML" in parsing.messages[-1] and str(bad) in parsing.messages[-1]


@pytest.mark.parametrize("source", ["xt", "tp"])
def test_eet_primary_source_is_retained_when_selected_migration_fails(project, parsing, source):
    bad = project.root / ("broken.xml" if source == "xt" else "broken.esp")
    bad.write_bytes(b"invalid source")
    cfg = _config(eet_path=str(project.source), **{f"{source}_path": str(bad)})
    notices = []
    parsing.host.show_message = lambda message, **options: notices.append((message, options))
    _run(project, parsing, cfg, "parse_eet")

    assert list(project.ctx.slots) == parse_fixtures.source_keys(project, [str(project.source)])
    assert not project.ctx.authoritative_projection_diverged()
    message, options = notices[-1]
    assert "迁移部分失败" in message and str(bad) in message
    assert options["error"] is True and callable(options["retry"])


@pytest.mark.parametrize("source", ["eet", "xt"])
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
def test_wrong_xml_format_is_reported_instead_of_registering_completed_migration(project, parsing, source, batch):
    cfg = _inputs(project.root, source, batch)
    selected = Path(getattr(cfg, f"{source}_path"))
    _xml(selected, "xt" if source == "eet" else "eet")

    _run(project, parsing, cfg, "batch_parse_esp" if batch else "parse_esp")

    assert list(project.ctx.slots) == parse_fixtures.source_keys(project, cfg.esp_paths)
    for slot in project.ctx.slots.values():
        assert getattr(slot, f"{source}_path") is None
        assert slot.migrate_count == 0
        assert all(not entry.translation for entry in slot.collection)
    assert not project.ctx.authoritative_projection_diverged()
    assert "迁移部分失败" in parsing.messages[-1]
    assert f"所选格式 xml.{source}" in parsing.messages[-1]


@pytest.mark.parametrize("source", ["eet", "xt"])
@pytest.mark.parametrize("empty", [False, True], ids=["unmatched", "empty"])
def test_valid_xml_without_matching_translations_remains_a_completed_migration(project, parsing, source, empty):
    cfg = _inputs(project.root, source, True)
    selected = Path(getattr(cfg, f"{source}_path"))
    if empty:
        root = "DocumentElement" if source == "eet" else "SSTXMLRessources"
        content = f"<{root}/>"
    else:
        content = selected.read_text(encoding="utf-8").replace("Npc", "Other").replace("Source ", "Unrelated ")
    selected.write_text(content, encoding="utf-8")

    _run(project, parsing, cfg, "batch_parse_esp")

    for slot in project.ctx.slots.values():
        assert getattr(slot, f"{source}_path") == str(selected)
        assert slot.migrate_count == 0
        assert all(not entry.translation for entry in slot.collection)
    assert not project.ctx.authoritative_projection_diverged()
    assert not any("失败" in message for message in parsing.messages)
