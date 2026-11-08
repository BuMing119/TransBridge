"""Exclusive output and explicit record editing in the EET binary experiment."""

from dataclasses import replace
import json
import struct

import pytest

from scripts.eet_lab import main
from transbridge.parser.eet_binary_codec import EetDocument, EetRecord


def _document():
    record = EetRecord(
        group="NPC_",
        form_id="00000800",
        editor_id="LabNpc",
        field="FULL",
        original="Original NPC",
        translation="旧译文",
        personal="Personal text",
        index=0,
        status=1,
        text_id=0,
        comment="Private comment",
        original_hash=0x12345678,
        normalized_original="original npc",
        normalized_hash=0x87654321,
        original_variables=b"",
        translation_variables=b"",
        icon=37,
    )
    return EetDocument(
        version=3,
        game="",
        records=(record, replace(record, form_id="00000801", editor_id="LabNpc2", index=1)),
        phrases=(("Private phrase", "私有短语"),),
    )


def _source(tmp_path, document=None):
    source = tmp_path / "source.eet"
    source.write_bytes((document or _document()).to_bytes())
    return source


def test_roundtrip_is_byte_identical_and_keeps_source(tmp_path, capsys):
    source = _source(tmp_path)
    original = source.read_bytes()
    output = tmp_path / "copy.eet"
    assert main(["roundtrip", str(source), "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["byte_identical"]
    assert result["output"] == str(output.resolve())
    assert source.read_bytes() == output.read_bytes() == original


@pytest.mark.parametrize("translation", ["新的译文 😀\r\n第二行", ""])
def test_patch_changes_only_requested_translation(tmp_path, capsys, translation):
    document = _document()
    source = _source(tmp_path, document)
    original = source.read_bytes()
    output = tmp_path / "patched.eet"
    assert main(["patch", str(source), "--record", "1", "--translation", translation, "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["byte_identical"]
    actual = EetDocument.from_bytes(output.read_bytes())
    expected = (document.records[0], replace(document.records[1], translation=translation))
    assert actual == replace(document, records=expected)
    assert source.read_bytes() == original


@pytest.mark.parametrize("command", ["patch", "roundtrip"])
@pytest.mark.parametrize("same_as_source", [True, False])
def test_existing_output_is_never_overwritten(tmp_path, command, same_as_source):
    source = _source(tmp_path)
    original = source.read_bytes()
    output = source if same_as_source else tmp_path / "existing.eet"
    if not same_as_source:
        output.write_bytes(b"existing unrelated output")
    existing = output.read_bytes()
    args = [command, str(source), "--output", str(output)]
    if command == "patch":
        args.extend(["--record", "0", "--translation", "replacement"])
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert output.read_bytes() == existing
    assert source.read_bytes() == original


@pytest.mark.parametrize("command", ["inspect", "patch"])
@pytest.mark.parametrize("ordinal", ["-1", "2"])
def test_invalid_ordinal_does_not_create_output(tmp_path, command, ordinal):
    source = _source(tmp_path)
    output = tmp_path / "invalid.eet"
    args = [command, str(source), "--record", ordinal]
    if command == "patch":
        args.extend(["--translation", "new", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert not output.exists()


def test_malformed_input_creates_no_output(tmp_path):
    source = _source(tmp_path)
    source.write_bytes(source.read_bytes()[:-1])
    output = tmp_path / "invalid.eet"
    with pytest.raises(SystemExit) as error:
        main(["roundtrip", str(source), "--output", str(output)])
    assert error.value.code == 2
    assert not output.exists()


def test_inspect_hides_text_unless_record_is_explicit(tmp_path, capsys):
    source = _source(tmp_path)
    assert main(["inspect", str(source)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["version"] == 3
    assert result["type_dt"] == 2
    assert result["records"] == 2
    assert result["phrases"] == 1
    assert "record" not in result
    assert not any("Private" in str(value) or "Original" in str(value) for value in result.values())
    assert main(["inspect", str(source), "--record", "0"]) == 0
    record = json.loads(capsys.readouterr().out)["record"]
    assert record["translation"] == "旧译文"
    assert record["comment"] == "Private comment"
    assert record["original_variables"] == ""
    assert record["translation_variables"] == ""


@pytest.mark.parametrize("variable_field", ["original_variables", "translation_variables"])
def test_variable_caches_roundtrip_and_patch_rebuilds_translation_cache(tmp_path, capsys, variable_field):
    document = _document()
    cache = struct.pack("<I", 3) + b"{0}" + struct.pack("<II", 1, 6) + "名字".encode()
    first = replace(document.records[0], **{variable_field: cache})
    document = replace(document, records=(first, document.records[1]))
    source = _source(tmp_path, document)
    copied = tmp_path / "copy.eet"
    assert main(["roundtrip", str(source), "--output", str(copied)]) == 0
    assert copied.read_bytes() == source.read_bytes()
    capsys.readouterr()
    assert main(["inspect", str(source), "--record", "0"]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["record"][variable_field] == cache.hex()
    assert inspected["number_caches"]["original" if variable_field == "original_variables" else "translation"] == {
        "template": "{0}",
        "values": ["名字"],
    }
    output = tmp_path / "patched.eet"
    assert main(["patch", str(source), "--record", "0", "--translation", "new 75", "--output", str(output)]) == 0
    actual = EetDocument.from_bytes(output.read_bytes())
    assert actual.records[0].translation_number_cache.template == "new %VAR00%"
    assert actual.records[0].translation_number_cache.values == ("75",)
    assert actual.records[0].original_variables == first.original_variables
    assert actual.records[1] == document.records[1]


@pytest.mark.parametrize(
    "mode,values", [("off", ("12",)), ("all", ("I", "III", "12")), ("exclude-i-space", ("III", "12"))]
)
def test_patch_cli_number_profile(tmp_path, capsys, mode, values):
    source = _source(tmp_path)
    output = tmp_path / "profile.eet"
    assert (
        main([
            "patch",
            str(source),
            "--record",
            "0",
            "--translation",
            "I III 12",
            "--roman-numbers",
            mode,
            "--max-number-length",
            "300",
            "--output",
            str(output),
        ])
        == 0
    )
    assert EetDocument.from_bytes(output.read_bytes()).records[0].translation_number_cache.values == values
    assert json.loads(capsys.readouterr().out)["number_options"]["max_utf16_units"] == 300


def test_invalid_number_profile_does_not_create_output(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "invalid-profile.eet"
    with pytest.raises(SystemExit) as error:
        main([
            "patch",
            str(source),
            "--record",
            "0",
            "--translation",
            "12",
            "--max-number-length",
            "-1",
            "--output",
            str(output),
        ])
    assert error.value.code == 2
    assert not output.exists()


def test_session_without_phrase_section_is_preserved(tmp_path, capsys):
    document = replace(_document(), type_dt=1, game="Skyrim SE", phrases=None)
    source = _source(tmp_path, document)
    output = tmp_path / "session-copy.eet"
    assert main(["roundtrip", str(source), "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["type_dt"] == 1
    assert result["game"] == "Skyrim SE"
    assert result["phrases"] == 0
    assert result["phrase_section_present"] is False
    assert EetDocument.from_bytes(output.read_bytes()) == document


def test_unverified_version_cannot_be_exported(tmp_path):
    source = _source(tmp_path)
    content = source.read_bytes()
    source.write_bytes(content[:8] + struct.pack("<I", 99) + content[12:])
    output = tmp_path / "unverified.eet"
    with pytest.raises(SystemExit) as error:
        main(["roundtrip", str(source), "--output", str(output)])
    assert error.value.code == 2
    assert not output.exists()
