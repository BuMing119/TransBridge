"""Safety and editing semantics of the opt-in SST experiment commands."""

from dataclasses import replace
import json

import pytest

from scripts.sst_lab import demo_document, main
from transbridge.parser.xt.sst_codec import SstDocument


def test_demo_and_roundtrip(tmp_path, capsys):
    source = tmp_path / "demo.sst"
    output = tmp_path / "copy.sst"
    assert main(["create-demo", "--output", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["records"] == 3
    assert main(["roundtrip", str(source), "--output", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["byte_identical"]
    assert source.read_bytes() == output.read_bytes()


@pytest.mark.parametrize("translation", ["译文变长 😀\r\n第二行", ""])
def test_patch_changes_only_selected_translation(tmp_path, capsys, translation):
    document = replace(demo_document(), flag=7, labels=((5, "Review"),))
    source = tmp_path / "source.sst"
    output = tmp_path / "patched.sst"
    original_bytes = document.to_bytes()
    source.write_bytes(original_bytes)
    assert main(["patch", str(source), "--record", "1", "--translation", translation, "--output", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["byte_identical"]
    changed = SstDocument.from_bytes(output.read_bytes())
    expected = list(document.records)
    expected[1] = replace(expected[1], translation=translation)
    assert changed == replace(document, records=tuple(expected))
    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("command", ["patch", "roundtrip", "create-demo"])
def test_existing_output_is_never_overwritten(tmp_path, command):
    source = tmp_path / "source.sst"
    original = demo_document().to_bytes()
    source.write_bytes(original)
    args = [command]
    if command != "create-demo":
        args.append(str(source))
    if command == "patch":
        args.extend(["--record", "0", "--translation", "new"])
    with pytest.raises(SystemExit) as error:
        main([*args, "--output", str(source)])
    assert error.value.code == 2
    assert source.read_bytes() == original


@pytest.mark.parametrize("ordinal", ["-1", "3"])
def test_invalid_ordinal_creates_no_output(tmp_path, ordinal):
    source = tmp_path / "source.sst"
    source.write_bytes(demo_document().to_bytes())
    output = tmp_path / "bad.sst"
    with pytest.raises(SystemExit) as error:
        main(["patch", str(source), "--record", ordinal, "--translation", "new", "--output", str(output)])
    assert error.value.code == 2
    assert not output.exists()


def test_bad_source_creates_no_output(tmp_path):
    source = tmp_path / "source.sst"
    source.write_bytes(demo_document().to_bytes()[:-1])
    output = tmp_path / "bad.sst"
    with pytest.raises(SystemExit) as error:
        main(["roundtrip", str(source), "--output", str(output)])
    assert error.value.code == 2
    assert not output.exists()


def test_inspect_does_not_show_text_unless_requested(tmp_path, capsys):
    source = tmp_path / "source.sst"
    source.write_bytes(demo_document().to_bytes())
    assert main(["inspect", str(source)]) == 0
    assert "record" not in json.loads(capsys.readouterr().out)
    assert main(["inspect", str(source), "--record", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["record"]["translation"] == "实验长剑"
