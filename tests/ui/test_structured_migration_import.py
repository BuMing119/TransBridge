from __future__ import annotations

from collections import Counter
from html import escape
import json
from pathlib import Path
import time
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.parser.xt.sst_parser import SST_Parser
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.workbench._parse_config_dialog import ParseConfigDialog

project = source_fixtures.project
_APP = QApplication.instance() or QApplication([])


def _until(project, predicate) -> None:
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        project.app.processEvents()
        time.sleep(0.005)
    project.app.processEvents()
    assert predicate()


def _coordinator(project):
    messages = []
    host = SimpleNamespace(
        context=project.ctx,
        workers=[],
        show_message=messages.append,
        workbench=SimpleNamespace(
            show_step2_progress=lambda *_args: None,
            hide_step2_progress=lambda: None,
        ),
    )
    return ParseCoordinator(host), host, messages


def _config(path):
    return SimpleNamespace(
        eet_path=None,
        xt_path=None,
        tp_path=None,
        strings_dir=None,
        strings_lang="chinese",
        strings_apply_all=False,
        json_path=str(path),
        json_format_id="json.paratranz",
        sst_path=None,
        sst_format_id=None,
    )


def _sst_config(path):
    config = _config(path)
    config.json_path = None
    config.json_format_id = None
    config.sst_path = str(path)
    config.sst_format_id = "sst.ssu8"
    return config


def test_dialog_prefill_carries_reviewed_json_and_sst_format_choices(tmp_path) -> None:
    dialog = ParseConfigDialog(mode="migrate")
    json_path = tmp_path / "source.json"
    assert dialog.prefill_migration_source(str(json_path), "json", "json.dsd")
    config = dialog.get_config()
    assert config.json_path == str(json_path)
    assert config.json_format_id == "json.dsd"

    sst_path = tmp_path / "source.sst"
    assert dialog.prefill_migration_source(str(sst_path), "sst", "sst.ssu9")
    config = dialog.get_config()
    assert config.sst_path == str(sst_path)
    assert config.sst_format_id == "sst.ssu9"
    dialog.close()


def test_reviewed_drop_format_reaches_the_migration_draft() -> None:
    calls = []
    composition = object.__new__(ShellIntentComposition)
    composition._host = SimpleNamespace(
        parse_coordinator=SimpleNamespace(apply_migration=lambda *args: calls.append(args))
    )

    composition._migrate_source({"path": "D:/migration.json", "drop_kind": "json", "format_id": "json.paratranz"})

    assert calls == [("D:/migration.json", "json", "json.paratranz")]


def test_real_v2_json_migration_commits_once_and_updates_visible_projection(project) -> None:
    target = source_fixtures.load_source(project)
    source = project.root / "migration.json"
    source.write_text(
        json.dumps([
            {
                "id": 101,
                "key": target.key,
                "original": target.original,
                "translation": "真实调用链译文",
                "stage": 1,
            }
        ]),
        encoding="utf-8",
    )
    coordinator, host, messages = _coordinator(project)
    before = project.services.project_lifecycle.active.variant.snapshot()

    coordinator._run_migrate(project.ctx.active_slot, _config(source))
    _until(project, lambda: host.workers and not host.workers[-1].isRunning())

    after = project.services.project_lifecycle.active.variant.snapshot()
    assert after.revision == before.revision + 1
    assert after.entries[0].translation == "真实调用链译文"
    assert project.ctx.collection.get(target.identity).translation == "真实调用链译文"
    assert project.ctx.dirty
    assert not project.ctx.authoritative_projection_diverged()
    assert any("新增 1 条译文" in message for message in messages)


def test_real_v2_ssu8_migration_uses_the_same_atomic_commit_chain(project) -> None:
    source = Path("tests/trans_exe/xt/ssu8/ccbgssse010-petdwarvenarmoredmudcrab_english_chinese.sst")
    parsed = SST_Parser.from_file(str(source)).entries
    counts = Counter((entry.form_id, entry.index) for entry in parsed)
    sst_entry = next(entry for entry in parsed if counts[(entry.form_id, entry.index)] == 1)
    project.source.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<DocumentElement><ESP>
<GRUP>NPC_</GRUP><ID>{sst_entry.form_id:08X}</ID><EDID>Mudcrab</EDID><CHAMP>FULL</CHAMP>
<ORIGINAL>{escape(sst_entry.text)}</ORIGINAL><TRADUIT></TRADUIT><PERSO></PERSO>
<INDEX>{sst_entry.index}</INDEX><STATUS>0</STATUS><IDSTEXTE>1</IDSTEXTE>
<COMMENTAIRE></COMMENTAIRE><ICON>0</ICON>
</ESP></DocumentElement>""",
        encoding="utf-8",
    )
    target = source_fixtures.load_source(project)
    coordinator, host, messages = _coordinator(project)
    before = project.services.project_lifecycle.active.variant.snapshot()

    coordinator._run_migrate(project.ctx.active_slot, _sst_config(source))
    _until(project, lambda: host.workers and not host.workers[-1].isRunning())

    after = project.services.project_lifecycle.active.variant.snapshot()
    assert after.revision == before.revision + 1
    assert after.entries[0].translation == sst_entry.translated_text
    assert project.ctx.collection.get(target.identity).translation == sst_entry.translated_text
    assert project.ctx.active_slot.sst_path == str(source)
    assert not project.ctx.authoritative_projection_diverged()
    assert any("sst.ssu8" in message and "新增 1 条译文" in message for message in messages)


@pytest.mark.parametrize(
    ("content", "diagnostic"),
    [
        ("{not-json", "PARATRANZ_JSON_INVALID"),
        ("[]", "MIGRATION_SOURCE_EMPTY"),
    ],
)
def test_failed_real_v2_json_migration_leaves_variant_and_slot_unchanged(project, content, diagnostic) -> None:
    source_fixtures.load_source(project)
    source = project.root / "bad-migration.json"
    source.write_text(content, encoding="utf-8")
    coordinator, host, messages = _coordinator(project)
    before_variant = project.services.project_lifecycle.active.variant.snapshot()
    before_slot = project.ctx.active_slot
    before_entries = tuple(entry.snapshot() for entry in before_slot.collection)

    coordinator._run_migrate(before_slot, _config(source))
    _until(project, lambda: host.workers and not host.workers[-1].isRunning())

    assert project.services.project_lifecycle.active.variant.snapshot() == before_variant
    assert project.ctx.active_slot is before_slot
    assert tuple(entry.snapshot() for entry in before_slot.collection) == before_entries
    assert any(diagnostic in message for message in messages)
