from argparse import Namespace
from io import BytesIO
import json
import struct
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from scripts.recover_skyrim_translations import run
from scripts.skyrim_translation_recovery.inputs import read_export
from scripts.skyrim_translation_recovery.matching import Decision, RemoteRow, TranslationIndex, recover
from scripts.skyrim_translation_recovery.project import locate, reconcile_shared_ids
from tests.integration.publish.test_bsa_localized_write import _field, _group, _record, _strings
from transbridge.application.io.identity import EntryKey, SourceNamespace


def row(text="修订译文", *, key="Quest:00000800|85~QUST:CNAM", original="First", stage=5, plugin="mod", ref="a"):
    return RemoteRow(ref, plugin, key, original, text, stage, {})


def test_exact_original_retains_revision_and_review_stage():
    decision = recover(TranslationIndex([row()]), "mod", row().key, "First", "旧译文")
    assert (decision.translation, decision.stage, decision.reason) == ("修订译文", 5, "para_exact")


def test_same_legacy_key_never_transfers_other_journal_original():
    decision = recover(TranslationIndex([row()]), "mod", row().key, "Second", "第二段")
    assert (decision.translation, decision.reason) == ("第二段", "ank")


def test_changed_ordinal_uses_same_field_original_without_crossing_plugins():
    index = TranslationIndex([row()])
    key = "Quest:00000800|99~QUST:CNAM"
    assert recover(index, "mod", key, "First", None).reason == "para_original"
    assert recover(index, "other", key, "First", "补全").reason == "ank"
    assert recover(index, "mod", key.replace("CNAM", "FULL"), "First", None).reason == "untranslated"


def test_conflicting_para_candidates_block_ank():
    index = TranslationIndex([row(ref="a"), row("另一修订", ref="b")])
    decision = recover(index, "mod", row().key, "First", "旧译文")
    assert decision.reason == "para_conflict"
    assert decision.translation == ""
    assert decision.references == ("a", "b")


@pytest.mark.parametrize("stage", [-1, 9])
def test_explicit_blank_hidden_or_locked_is_preserved(stage):
    decision = recover(TranslationIndex([row("", stage=stage)]), "mod", row().key, "First", "补全")
    assert (decision.translation, decision.stage) == ("", stage)


def test_nonempty_stage_zero_is_not_replaced_or_promoted():
    decision = recover(TranslationIndex([row("草稿", stage=0)]), "mod", row().key, "First", "补全")
    assert (decision.translation, decision.stage) == ("草稿", 0)
    moved = recover(TranslationIndex([row("草稿", stage=0)]), "mod", "Other:00000800|99~QUST:CNAM", "First", "补全")
    assert (moved.translation, moved.stage) == ("草稿", 0)


def entry(key, original="First", sid=148):
    return SimpleNamespace(entry_key=EntryKey(SourceNamespace.legacy(), key), original=original, string_id=sid)


def test_equal_numeric_ids_use_separate_tables():
    tables = {".strings": {148: "First"}, ".dlstrings": {148: "Second"}, ".ilstrings": {}}
    assert locate(entry("Item:00000800|1~WEAP:FULL"), tables) == (".strings", 148)
    assert locate(entry("Item:00000800|1~WEAP:DESC", "Second"), tables) == (".dlstrings", 148)


def test_shared_physical_id_preserves_para_over_fallback_and_blocks_disagreement():
    entries = [entry("a"), entry("b")]
    locations = [(".strings", 148)] * 2
    decisions = [Decision("修订", 5, "para_exact", ("a",)), Decision("旧译", 1, "ank")]
    reconcile_shared_ids(entries, locations, decisions)
    assert [d.translation for d in decisions] == ["修订", "修订"]
    decisions[1] = Decision("分歧", 5, "para_exact", ("b",))
    reconcile_shared_ids(entries, locations, decisions)
    assert all(d.reason == "shared_id_conflict" and not d.translation for d in decisions)


def export_bytes(rows, *, raw=None):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("utf8/mod.json", json.dumps(rows))
        archive.writestr("raw/mod.json.json", json.dumps(rows if raw is None else raw))
    return buffer.getvalue()


def test_export_uses_only_one_representation_and_retains_opaque_fields():
    payload = [{"key": "x", "original": "a", "translation": "b", "stage": 5, "custom": "retained"}]
    rows = read_export(export_bytes(payload), {"mod"})
    assert len(rows) == 1 and rows[0].payload["custom"] == "retained"
    with pytest.raises(ValueError, match="disagree"):
        read_export(export_bytes(payload, raw=[]), {"mod"})


def fixture_inputs(tmp_path):
    source = tmp_path / "game"
    source.mkdir()
    plugin = source / "mod.esp"
    weapon = _record("WEAP", 0x800, _field("EDID", b"Item\0") + _field("FULL", struct.pack("<I", 148)))
    plugin.write_bytes(
        _record("TES4", 0, _field("HEDR", struct.pack("<fII", 1.7, 1, 0x801)), 0x80) + _group("WEAP", weapon)
    )
    strings = source / "Strings"
    strings.mkdir()
    ank = tmp_path / "ank"
    ank.mkdir()
    for suffix in ("strings", "dlstrings", "ilstrings"):
        originals = {148: b"Sword", 149: b"Unrepresented"} if suffix == "strings" else {}
        (strings / f"mod_English.{suffix}").write_bytes(_strings(originals, suffix))
        translated = {148: "旧剑".encode(), 149: "未提取".encode()} if suffix == "strings" else {}
        (ank / f"mod_English.{suffix}").write_bytes(_strings(translated, suffix))
    project = tmp_path / "old-project.json"
    project.write_text(json.dumps({"data": {"sources": [{"format_id": "plugin.sse", "location": str(plugin)}]}}))
    archive = tmp_path / "export.zip"
    archive.write_bytes(
        export_bytes([
            {"key": "Item:00000800|1~WEAP:FULL", "original": "Sword", "translation": "新剑", "stage": 5},
            {"key": "Removed", "original": "Gone", "translation": "保留修订", "stage": 5},
        ])
    )
    return Namespace(
        project=project,
        paratranz=archive,
        ank=ank,
        output=tmp_path / "result",
        language="english",
        name="恢复工程",
        apply=False,
        install_root=None,
    )


def test_preview_then_independent_project_import_preserves_inputs_and_all_revisions(tmp_path, monkeypatch):
    # Keep tempfile users within the fixture directory, including Windows sandboxes.
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    args = fixture_inputs(tmp_path)
    inputs = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    report = run(args)
    assert report["status"] == "preview" and report["counts"] == {"para_exact": 1}
    assert not (args.output / "recovered.transbridge").exists()
    assert report["unmatched_para_nonempty"] == 1
    assert report["sources"][0]["unrepresented_ank_candidates"][0]["ank"] == "未提取"
    with pytest.raises(FileExistsError):
        run(args)
    args.output = tmp_path / "applied"
    args.apply = True
    args.install_root = tmp_path / "installed"
    report = run(args)
    assert report["status"] == "installed" and report["installed_project_path"]
    with ZipFile(report["archive"]) as archive:
        document = json.loads(archive.read("archive.json"))
        states = document["variants"][0]["data"]["entries"]
        assert len(states) == 1
        assert (states[0]["translation"], states[0]["stage"]) == ("新剑", 5)
        assert states[0]["provenance"][0]["source"] == "para_exact"
        assert any(name.endswith("mod_English.strings") for name in archive.namelist())
    assert all(p.read_bytes() == content for p, content in inputs.items())
    assert not (args.install_root / "active-project.json").exists()
    assert "保留修订" in (args.output / "unmatched-paratranz.json").read_text(encoding="utf-8")
