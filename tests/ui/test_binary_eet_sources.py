"""Content-based EET routing through real GUI workers and saved-source hydration."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QWidget
import pytest

from tests.parser.test_eet_binary_codec import _wire
from tests.plugin_fixtures import write_plugin
from tests.ui.test_parse_hydration_authority import (
    _until,
    parsing as parsing_fixture,
    project as project_fixture,
)
from transbridge.application.io import FormatId
from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.ui.context import AppContext
from transbridge.ui.coordinators.migration_coordinator import run_migration
from transbridge.ui.coordinators.source_input_coordinator import SourceInputCoordinator
from transbridge.ui.eet_source import parse_eet_collection
from transbridge.ui.source_hydration import prepare_project_slots
from transbridge.ui.workbench.step1 import Step1SourceWidget

project = project_fixture
parsing = parsing_fixture


def binary_source(path, *, translation="", duplicates=False):
    document = EetDocument.from_bytes(_wire())
    record = replace(
        document.records[0],
        group="NPC_",
        form_id="00000800",
        editor_id="Greeting",
        field="FULL",
        original="Hello",
        translation=translation,
    )
    document = replace(document, records=(record, record) if duplicates else (record,))
    path.write_bytes(document.to_bytes())
    return document


def config(path=None):
    return SimpleNamespace(
        esp_paths=[],
        eet_path=str(path) if path else None,
        xt_path=None,
        tp_path=None,
        strings_dir=None,
        strings_lang="english",
        strings_apply_all=False,
        skip_empty=True,
        json_path=None,
        json_format_id=None,
        sst_path=None,
        sst_format_id=None,
    )


@pytest.mark.parametrize("suffix", [".eet", ".xml"])
def test_gui_binary_eet_import_reopens_from_saved_snapshot(project, parsing, suffix):
    path = project.root / f"dictionary{suffix}"
    document = binary_source(path, duplicates=True)
    parsing.coordinator._run_parse_eet(config(path))
    _until(project, lambda: not parsing.state["parsing"])
    assert len(project.ctx.slots) == 1, parsing.messages
    slot = project.ctx.active_slot
    assert slot.format_id is FormatId.BINARY_EET
    assert slot.source_snapshot.content == document.to_bytes()
    assert slot.eet_path == str(path)
    assert len(slot.collection) == 2
    assert len({entry.identity for entry in slot.collection}) == 2
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.gui_project_commands.save(project.request).is_success
    path.unlink()
    opened = project.services.current_project_opener.prepare_active(project.request)
    assert opened.is_success and opened.value.recovery is None, opened.diagnostics
    values = dict(project.services.project_projection.snapshot().values)
    values["sources"] = opened.value.sources
    slots = prepare_project_slots(opened.value.hydrations, SimpleNamespace(values=values))
    assert len(slots) == 1
    restored = slots[0][1]
    assert restored.format_id is FormatId.BINARY_EET
    assert restored.eet_path is None
    assert restored.source_snapshot.content == document.to_bytes()
    assert {entry.identity for entry in restored.collection} == {entry.identity for entry in slot.collection}


def test_xml_content_with_eet_extension_keeps_xml_format(tmp_path):
    path = tmp_path / "translation.eet"
    path.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    collection, parsed = parse_eet_collection(path)
    assert parsed.format_id is FormatId.XML_EET and len(collection) == 1


@pytest.mark.parametrize("content", [b"unknown binary\0", b"EET_", b"<OtherRoot/>"])
def test_unknown_or_broken_eet_is_not_silently_parsed_as_xml(tmp_path, content):
    path = tmp_path / "broken.eet"
    path.write_bytes(content)
    with pytest.raises(ValueError):
        parse_eet_collection(path)


@pytest.mark.parametrize("source_kind", ["plugin", "binary"])
@pytest.mark.parametrize("ambiguous", [False, True])
def test_binary_migration_uses_unique_match_and_authoritative_command(
    project, parsing, monkeypatch, source_kind, ambiguous
):
    if source_kind == "plugin":
        source = write_plugin(project.root / "source.esp", [(0x800, "Greeting", "Hello")])
        cfg = config()
        cfg.esp_paths = [str(source)]
        parsing.coordinator._run_parse_esp(cfg)
    else:
        source = project.root / "source.eet"
        binary_source(source)
        parsing.coordinator._run_parse_eet(config(source))
    _until(project, lambda: not parsing.state["parsing"])
    slot = project.ctx.active_slot
    assert slot is not None, parsing.messages
    original = next(iter(slot.collection))
    path = project.root / "translated.eet"
    binary_source(path, translation="Imported", duplicates=ambiguous)
    monkeypatch.setattr(
        "transbridge.ui.coordinators.migration_coordinator.legacy_migration.prepare_legacy_migration",
        lambda *_a, **_k: pytest.fail("Binary EET must not reach the XML migration adapter"),
    )
    parsing.messages.clear()
    run_migration(parsing.host, slot, config(path))
    _until(project, lambda: bool(parsing.messages))
    migrated = slot.collection.get(original.identity)
    expected = "" if ambiguous else "Imported"
    assert migrated.translation == expected, parsing.messages
    assert migrated.metadata == original.metadata
    assert project.services.project_lifecycle.active.variant.snapshot().entries[0].translation == expected
    assert not project.ctx.authoritative_projection_diverged()
    if source_kind == "binary":
        assert slot.eet_path == str(source)
    elif not ambiguous:
        assert slot.eet_path == str(path)


def test_legacy_step1_picker_and_worker_keep_binary_snapshot(project, monkeypatch):
    path = project.root / "legacy.eet"
    document = binary_source(path)
    context = AppContext()
    widget = Step1SourceWidget(context)
    filters = []

    def choose(_parent, _title, _directory, file_filter):
        filters.append(file_filter)
        return str(path), ""

    monkeypatch.setattr("transbridge.ui.workbench.source_input_view.QFileDialog.getOpenFileName", choose)
    widget._source_view.browse_eet()
    assert "*.eet" in filters[0] and "*.xml" in filters[0]
    assert "ESP-ESM Translator EET (*.eet);;ESP-ESM Translator XML (*.xml)" in filters[0]
    completed = []
    widget.parse_finished.connect(completed.append)
    widget._run_parse_eet(str(path))
    _until(project, lambda: bool(completed))
    assert context.active_slot.format_id is FormatId.BINARY_EET
    assert context.active_slot.source_snapshot.content == document.to_bytes()
    for worker in widget._workers:
        _until(project, lambda w=worker: not w.isRunning())
    widget.close()


def test_source_supplement_format_picker_offers_binary_eet(project, monkeypatch):
    host = QWidget()
    slot = SimpleNamespace(
        label="Saved",
        source_namespace="source",
        source_id="source-id",
        source_snapshot=None,
        format_id=None,
        collection=(),
    )
    host.context = SimpleNamespace(active_version_identity=("project", "variant"), slots={"eet": slot})
    host.runtime_context = project.request
    choices = []

    def choose(_parent, _title, _prompt, labels, *_args):
        choices.extend(labels)
        return "", False

    monkeypatch.setattr("transbridge.ui.coordinators.source_input_coordinator.QInputDialog.getItem", choose)
    coordinator = SourceInputCoordinator(host)
    coordinator._ensure_one("template", ("eet",), resume=lambda: None, selected_key="eet")
    assert "ESP-ESM Translator EET (.eet，实验性)" in choices
    assert not coordinator._busy
    host.close()
