"""Real source-checkout CLI smoke tests using only synthetic plugin/EET files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys

import pytest

from tests.plugin_fixtures import _field, _record


def _plugin(path: Path) -> Path:
    npc = _record(b"NPC_", 0x801, _field(b"EDID", "TestNpc") + _field(b"FULL", "Test NPC"))
    quest = _record(b"QUST", 0x800, _field(b"EDID", "TestQuest") + _field(b"FULL", "Quest Original"))
    groups = b"".join(
        struct.pack("<4sI4siHHI", b"GRUP", 24 + len(record), kind, 0, 0, 0, 0) + record
        for kind, record in ((b"NPC_", npc), (b"QUST", quest))
    )
    path.write_bytes(_record(b"TES4", 0, b"") + groups)
    return path


def _run_script(tmp_path: Path, script: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [sys.executable, str(root / "scripts" / script), *arguments],
        cwd=tmp_path,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("with_eet", [False, True])
def test_categorized_scripts_run_with_real_plugin_and_optional_eet(tmp_path: Path, with_eet: bool) -> None:
    plugin = _plugin(tmp_path / "Test.esp")
    original = plugin.read_bytes()
    output = tmp_path / "分类输出"
    script = "plugin_eet_to_json.py" if with_eet else "convert_plugin_to_jsons.py"
    arguments = [str(plugin)]
    if with_eet:
        eet = tmp_path / "translation.xml"
        eet.write_text(
            "<DocumentElement><ESP><GRUP>NPC_</GRUP><ID>00000801</ID><EDID>TestNpc</EDID>"
            "<CHAMP>FULL</CHAMP><ORIGINAL>Test NPC</ORIGINAL><TRADUIT>测试人物</TRADUIT>"
            "<INDEX>1</INDEX><STATUS>1</STATUS></ESP></DocumentElement>",
            encoding="utf-8",
        )
        arguments.append(str(eet))
    arguments.extend((str(output), "--indent", "4"))

    result = _run_script(tmp_path, script, arguments)

    assert result.returncode == 0, result.stdout + result.stderr
    npc = json.loads((output / "人名.json").read_text(encoding="utf-8"))[0]
    quest = json.loads((output / "任务/任务日志.json").read_text(encoding="utf-8"))[0]
    assert npc["original"] == "Test NPC"
    assert npc["translation"] == ("测试人物" if with_eet else "")
    assert quest["original"] == "Quest Original"
    assert quest["translation"] == ""
    assert plugin.read_bytes() == original


@pytest.mark.parametrize("with_eet", [False, True])
@pytest.mark.parametrize("invalid_input", ["missing", "directory", "damaged"])
def test_script_plugin_errors_exit_nonzero_without_output(tmp_path: Path, with_eet: bool, invalid_input: str) -> None:
    plugin = tmp_path / "Invalid.esp"
    if invalid_input == "directory":
        plugin.mkdir()
    elif invalid_input == "damaged":
        plugin.write_bytes(b"TES4\xff\xff\xff\xff")
    output = tmp_path / "output"
    script = "plugin_eet_to_json.py" if with_eet else "convert_plugin_to_jsons.py"
    arguments = [str(plugin)]
    if with_eet:
        eet = tmp_path / "translation.xml"
        eet.write_text("<DocumentElement />", encoding="utf-8")
        arguments.append(str(eet))
    arguments.append(str(output))

    result = _run_script(tmp_path, script, arguments)

    assert result.returncode != 0
    assert "Traceback" in result.stderr
    assert not output.exists()
    assert "已导出" not in result.stdout


@pytest.mark.parametrize("invalid_input", ["missing", "damaged"])
def test_script_eet_errors_exit_nonzero_without_output(tmp_path: Path, invalid_input: str) -> None:
    plugin = _plugin(tmp_path / "Test.esp")
    eet = tmp_path / "translation.xml"
    if invalid_input == "damaged":
        eet.write_text("<DocumentElement><ESP>", encoding="utf-8")
    output = tmp_path / "output"

    result = _run_script(tmp_path, "plugin_eet_to_json.py", [str(plugin), str(eet), str(output)])

    assert result.returncode != 0
    assert "Traceback" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("with_eet", [False, True])
def test_script_functions_propagate_original_plugin_error_and_cause(
    tmp_path: Path, monkeypatch, with_eet: bool
) -> None:
    from scripts import convert_plugin_to_jsons, plugin_eet_to_json

    module = plugin_eet_to_json if with_eet else convert_plugin_to_jsons
    cause = OSError("original loader failure")
    original_error = RuntimeError("plugin parsing failed")
    parse_calls = []

    def fail(path, **options):
        parse_calls.append((path, options))
        raise original_error from cause

    monkeypatch.setattr(module.PluginParser, "parse_plugin", lambda self, path, **options: fail(path, **options))
    plugin = tmp_path / "broken.esp"
    output = tmp_path / "output"
    with pytest.raises(RuntimeError) as caught:
        if with_eet:
            plugin_eet_to_json.plugin_eet_to_json(plugin, tmp_path / "translation.xml", output)
        else:
            convert_plugin_to_jsons.main([str(plugin), str(output)])

    assert caught.value is original_error
    assert caught.value.__cause__ is cause
    assert len(parse_calls) == 1
    assert parse_calls[0][1]["raise_on_error"] is True
    assert not output.exists()
