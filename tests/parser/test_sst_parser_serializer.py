"""Public SST API regressions; wire-layout tests live in test_sst_lab_codec."""

import csv
from dataclasses import replace
import json
from pathlib import Path

import pytest

from transbridge.parser.xt import SST_Entry, SST_Parser, SST_Serializer, SstDocument, SstRecord, sst_string_hash


def _record(**changes):
    values = dict(
        list_index=2,
        string_id=-1,
        form_id=0x12345678,
        record_type="INFO",
        field_type="NAM1",
        index=511,
        index_max=1023,
        edid_hash=0xABCDEF01,
        collab_id=7,
        params=0x49,
        original="Hello\r\n😀",
        translation="你好",
    )
    values.update(changes)
    return SstRecord(**values)


def _document(*records):
    return SstDocument(b"SSU9", 0xAB, ("Skyrim.esm", "Example.esp"), ((7, "作者"),), tuple(records))


def test_public_parser_maps_complete_metadata_and_one_based_index():
    document = _document(_record(), _record(record_type="z123", field_type="zzzz", original="", translation=""))
    parser = SST_Parser.from_bytes(document.to_bytes())
    assert parser.document == document
    assert len(parser) == 2
    entry = parser.entries[0]
    assert entry.record == document.records[0]
    assert (entry.index, entry.group_index, entry.global_seq, entry.f2) == (512, 1023, -1, 0xABCDEF01)
    assert entry.ordinal == 0
    assert entry.subrecords == ()
    assert parser.entries[1].rec == "z123zzzz"
    assert parser.entries[1].ordinal == 1
    assert SST_Serializer.from_parser(parser).to_bytes() == document.to_bytes()


def test_parser_retains_from_file_and_source_error_context(tmp_path):
    path = tmp_path / "vocab.sst"
    path.write_bytes(_document(_record()).to_bytes())
    assert SST_Parser.from_file(path).document == _document(_record())
    with pytest.raises(ValueError, match="damaged.sst") as error:
        SST_Parser.from_bytes(path.read_bytes()[:-1], source_name="damaged.sst")
    assert isinstance(error.value.__cause__, ValueError)


def test_parser_export_unknown_signature_and_csv(tmp_path):
    parser = SST_Parser.from_document(_document(_record(record_type="zzzz", field_type="xxxx")))
    data = json.loads(parser.to_json())
    assert data["entries"][0]["rec"] == "zzzz:xxxx"
    assert data["entries"][0]["params"] == 0x49
    path = tmp_path / "records.csv"
    parser.to_csv_file(path)
    with path.open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["translated_text"] == "你好"
    assert row["ordinal"] == "0"


@pytest.mark.parametrize("magic", [b"SSU8", b"SSU9"])
def test_create_new_and_update_empty_translation_preserve_all_metadata(tmp_path, magic):
    record = _record()
    writer = SST_Serializer.create_new("New.esp", [record], magic=magic, labels=((7, "作者"),), flag=17)
    assert writer.document.masters == (("New.esp",) if magic == b"SSU9" else ())
    assert writer.update_record(0, translated_text="")
    path = tmp_path / "new.sst"
    writer.save(path)
    result = SST_Parser.from_file(path).document
    assert result.records == (replace(record, translation=""),)
    assert (result.flag, result.labels) == (17, ((7, "作者"),))


def test_facade_edits_preserve_wire_fields():
    original = _record()
    parser = SST_Parser.from_document(_document(original))
    parser.entries[0] = replace(parser.entries[0], translated_text="", text="Changed")
    assert SST_Serializer.from_parser(parser).document.records == (
        replace(original, original="Changed", translation=""),
    )


def test_facade_create_new_and_hash_are_compatible():
    entry = SST_Entry("NPC_FULL", 0x800, "Hello", translated_text="你好")
    writer = SST_Serializer.create_new("New.esp", [entry])
    assert SST_Parser.from_bytes(writer.to_bytes()).entries[0].index == 1
    assert sst_string_hash("Ā") == sst_string_hash("\0")


def test_ambiguous_form_id_rejected_without_output_or_mutation(tmp_path):
    document = _document(_record(), _record(field_type="FULL"))
    writer = SST_Serializer.from_document(document)
    with pytest.raises(ValueError, match="Ambiguous"):
        writer.update_and_save(0x12345678, "wrong", tmp_path / "bad.sst")
    assert writer.document == document
    assert not (tmp_path / "bad.sst").exists()


def test_composite_or_ordinal_update_targets_one_record(tmp_path):
    records = (_record(), _record(field_type="FULL"), _record())
    writer = SST_Serializer.from_document(_document(*records))
    stats = writer.update_entries(
        [
            {"rec": "INFOFULL", "form_id": 0x12345678, "index": 512, "translated_text": "Full"},
            {"ordinal": 2, "translated_text": ""},
        ],
        tmp_path / "edited.sst",
    )
    assert stats == {"matched": 2, "updated": 2, "not_found": []}
    assert writer.document.records == (
        records[0],
        replace(records[1], translation="Full"),
        replace(records[2], translation=""),
    )


@pytest.mark.parametrize(
    "updates",
    [
        [{"ordinal": 0, "translated_text": "new"}, {"ordinal": 0, "translated_text": "other"}],
        [{"rec": "INFONAM1", "form_id": 0x12345678, "translated_text": "new"}],
        [{"ordinal": True, "translated_text": "new"}],
        [{"ordinal": 0, "translated_text": None}],
        [{"ordinal": 0, "translation": "typo"}],
    ],
)
def test_invalid_batch_is_atomic(tmp_path, updates):
    document = _document(_record())
    writer = SST_Serializer.from_document(document)
    with pytest.raises(ValueError):
        writer.update_entries(updates, tmp_path / "bad.sst")
    assert writer.document == document
    assert not list(tmp_path.iterdir())


def test_failed_save_preserves_document_and_existing_files(tmp_path):
    document = _document(_record())
    writer = SST_Serializer.from_document(document)
    path = tmp_path / "existing.sst"
    path.write_bytes(b"existing user file")
    unrelated = tmp_path / "existing.tmp"
    unrelated.write_bytes(b"unrelated")
    with pytest.raises(FileExistsError):
        writer.update_entries([{"ordinal": 0, "translated_text": "changed"}], path)
    assert path.read_bytes() == b"existing user file"
    assert unrelated.read_bytes() == b"unrelated"
    assert writer.document == document
    writer.save(path, overwrite=True)
    assert path.read_bytes() == document.to_bytes()
    assert set(tmp_path.iterdir()) == {path, unrelated}


def test_real_sample_has_all_8604_flat_records_and_preserves_bytes():
    path = Path(__file__).parents[1] / "trans_exe/xt/hlioremi_english_chinese.sst"
    if not path.exists():
        pytest.skip("Local xTranslator interoperability sample is not distributed")
    data = path.read_bytes()
    parser = SST_Parser.from_bytes(data)
    assert len(parser) == 8604
    assert all(entry.record is not None and not entry.subrecords for entry in parser.entries)
    assert SST_Serializer.from_parser(parser).to_bytes() == data
