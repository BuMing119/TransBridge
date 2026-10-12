"""Real plugin publication at the FOMOD packaging boundary."""

from dataclasses import replace
from pathlib import Path
import struct
import threading
import zipfile

import pytest

from tests.integration.publish.test_plugin_fidelity import _localized_quest, _texts
from tests.parser.test_original_match_plugin import quest_plugin
from tests.plugin_fixtures import write_plugin
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io import EntryKey, SourceNamespace, SsePluginAdapter
from transbridge.application.io.publish import ImmediateCommitGuard, OsPublishFilesystem
from transbridge.application.tasks import TaskCancelled
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.fomod.pipeline import FomodPipeline
from transbridge.fomod.plugin_publishing import prepare_plugin_source, publish_plugin_collection
from transbridge.translation_memory import TranslationMemoryManager
from transbridge.writer.plugin_writer import PluginWriter


def _prepared(tmp_path, localized=False):
    plugin = (
        _localized_quest(tmp_path / "source.esp")
        if localized
        else quest_plugin(tmp_path / "source.esp", ["First", "Second"])
    )
    source = prepare_plugin_source(plugin, namespace=SourceNamespace("fomod:slot"))
    collection = TranslationEntryCollection(
        tuple(
            replace(entry, translation=f"Translated {entry.original}", stage=1) for entry in source.collection_entries()
        )
    )
    return plugin, source, collection


@pytest.mark.parametrize("localized", [False, True])
def test_successful_publication_leaves_only_package_assets(tmp_path, localized):
    plugin, source, collection = _prepared(tmp_path, localized)
    before_files = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    result = publish_plugin_collection(plugin, collection, source=source, run_id="fomod-publish")
    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(plugin, localized=localized)[:2] == ["Translated First", "Translated Second"]
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before_files
    assert not any(path.name.startswith(".transbridge-") for path in tmp_path.rglob("*"))


def test_failed_renderer_cannot_truncate_existing_workspace_plugin(tmp_path, monkeypatch):
    plugin, source, collection = _prepared(tmp_path)
    original = plugin.read_bytes()

    def broken_write(_writer, output_path):
        Path(output_path).write_bytes(b"truncated plugin")
        raise ValueError("injected writer fault after truncation")

    monkeypatch.setattr(PluginWriter, "write", broken_write)
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_WRITE_FAILED:.*RENDER_FAILED"):
        FomodPipeline()._write_back(plugin, collection, source=source, run_id="fomod-publish")
    assert plugin.read_bytes() == original
    assert list(tmp_path.iterdir()) == [plugin]


def test_successful_renderer_with_wrong_translations_fails_fidelity_and_preserves_source(tmp_path, monkeypatch):
    plugin, source, collection = _prepared(tmp_path)
    original = plugin.read_bytes()
    write = PluginWriter.write

    def corrupt_write(writer, output_path):
        result = write(writer, output_path)
        path = Path(output_path)
        path.write_bytes(path.read_bytes().replace(b"Translated First", b"Translated Wrong"))
        return result

    monkeypatch.setattr(PluginWriter, "write", corrupt_write)
    with pytest.raises(RuntimeError, match="FIDELITY_MISMATCH"):
        publish_plugin_collection(plugin, collection, source=source)
    assert plugin.read_bytes() == original
    assert list(tmp_path.iterdir()) == [plugin]


@pytest.mark.parametrize("change", ["missing", "extra", "namespace", "original", "localized_id"])
def test_incomplete_or_unrelated_collection_is_blocked_instead_of_silently_skipped(tmp_path, change):
    plugin, source, collection = _prepared(tmp_path)
    original = plugin.read_bytes()
    entries = list(collection)
    if change == "missing":
        entries.pop()
    elif change == "extra":
        entries.append(
            replace(
                entries[0], id="unknown", key="unknown", entry_key=EntryKey(entries[0].identity.namespace, "unknown")
            )
        )
    elif change == "namespace":
        entries[0] = replace(
            entries[0], entry_key=EntryKey(SourceNamespace("foreign:source"), entries[0].key, entries[0].original)
        )
    elif change == "original":
        entries[0] = replace(
            entries[0], original="changed", entry_key=EntryKey(entries[0].identity.namespace, entries[0].key, "changed")
        )
    else:
        entries[0] = replace(entries[0], string_id=123)
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_(IDENTITY|LOCATOR)_MISMATCH"):
        publish_plugin_collection(plugin, TranslationEntryCollection(entries), source=source)
    assert plugin.read_bytes() == original
    assert list(tmp_path.iterdir()) == [plugin]


@pytest.mark.parametrize("localized", [False, True])
def test_changed_captured_source_or_companion_is_rejected(tmp_path, localized):
    plugin, source, collection = _prepared(tmp_path, localized)
    changed = tmp_path / "Strings" / "source_English.dlstrings" if localized else plugin
    changed.write_bytes(b"external replacement")
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_SOURCE_CHANGED"):
        publish_plugin_collection(plugin, collection, source=source)
    assert changed.read_bytes() == b"external replacement"


@pytest.mark.parametrize("phase", ["before", "after_render"])
def test_cancellation_blocks_publication_and_keeps_source(tmp_path, monkeypatch, phase):
    plugin, source, collection = _prepared(tmp_path)
    original = plugin.read_bytes()
    stop = threading.Event()
    if phase == "before":
        stop.set()
    else:
        write = PluginWriter.write

        def stop_after_render(writer, path):
            result = write(writer, path)
            stop.set()
            return result

        monkeypatch.setattr(PluginWriter, "write", stop_after_render)
    with pytest.raises(TaskCancelled):
        publish_plugin_collection(plugin, collection, source=source, cancellation=stop)
    assert plugin.read_bytes() == original
    assert list(tmp_path.iterdir()) == [plugin]


def test_partial_durability_result_blocks_packaging(tmp_path, monkeypatch):
    plugin, source, collection = _prepared(tmp_path)

    def fail_sync(_filesystem, _path):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(OsPublishFilesystem, "fsync_directory", fail_sync)
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_WRITE_FAILED:.*DIRECTORY_FSYNC_FAILED"):
        publish_plugin_collection(plugin, collection, source=source)


@pytest.mark.parametrize("localized", [False, True])
def test_full_pipeline_archive_contains_no_publication_backups_or_staging(tmp_path, localized):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    source = (
        _localized_quest(inputs / "source.esp")
        if localized
        else write_plugin(inputs / "source.esp", [(0x800, "Npc", "First")])
    )
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in inputs.rglob("*"):
            if path.is_file():
                bundle.write(path, path.relative_to(inputs).as_posix())
    output = tmp_path / "translated.zip"
    pipeline = FomodPipeline(tm_manager=TranslationMemoryManager(tmp_path / "empty-dictionary"))
    result = pipeline.run(
        str(archive), str(output), work_dir=str(tmp_path / "work"), target_lang="zh_CN", ai_enabled=False
    )
    assert result.plugins_processed == 1
    with zipfile.ZipFile(output) as bundle:
        names = bundle.namelist()
        assert "source.esp" in names
        assert not any(name.endswith((".bak", ".stage", ".log")) or ".transbridge-publish-" in name for name in names)
    assert source.exists()


def test_failed_plugin_publication_prevents_final_archive(tmp_path, monkeypatch):
    source = write_plugin(tmp_path / "source.esp", [(0x800, "Npc", "First")])
    archive = tmp_path / "new.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(source, source.name)
    original_archive = archive.read_bytes()
    output = tmp_path / "translated.zip"

    def broken_write(_writer, path):
        Path(path).write_bytes(b"truncated staging bytes")
        raise ValueError("injected renderer failure")

    monkeypatch.setattr(PluginWriter, "write", broken_write)
    pipeline = FomodPipeline(tm_manager=TranslationMemoryManager(tmp_path / "empty-dictionary"))
    with pytest.raises(RuntimeError, match="FOMOD_PIPELINE_FAILED"):
        pipeline.run(str(archive), str(output), work_dir=str(tmp_path / "work"), target_lang="zh_CN", ai_enabled=False)
    assert not output.exists()
    assert archive.read_bytes() == original_archive
    extracted = next((tmp_path / "work").rglob("source.esp"))
    assert extracted.read_bytes() == source.read_bytes()


def test_empty_original_is_kept_in_the_strict_collection(tmp_path):
    plugin = write_plugin(tmp_path / "source.esp", [(0x800, "Empty", ""), (0x801, "Normal", "First")])
    source = prepare_plugin_source(plugin)
    assert len(source.entries) == 2
    collection = TranslationEntryCollection(
        replace(entry, translation="Translated", stage=1) if entry.original else entry
        for entry in source.collection_entries()
    )
    assert publish_plugin_collection(plugin, collection, source=source).outcome is OperationOutcome.COMPLETED
    assert _texts(plugin) == ["", "Translated"]


def test_preexisting_unused_strings_is_preserved_without_false_change_error(tmp_path):
    plugin = quest_plugin(tmp_path / "source.esp", ["First", "Second"])
    extra = tmp_path / "Strings" / "source_English.strings"
    extra.parent.mkdir()
    payload = b"Unused companion text\0"
    original = struct.pack("<IIII", 1, len(payload), 777, 0) + payload
    extra.write_bytes(original)
    source = prepare_plugin_source(plugin)
    collection = TranslationEntryCollection(
        replace(entry, translation=f"Translated {entry.original}", stage=1) for entry in source.collection_entries()
    )
    assert publish_plugin_collection(plugin, collection, source=source).outcome is OperationOutcome.COMPLETED
    assert _texts(plugin) == ["Translated First", "Translated Second"]
    assert extra.read_bytes() == original


def test_companion_added_while_capturing_snapshot_is_rejected(tmp_path, monkeypatch):
    plugin = quest_plugin(tmp_path / "source.esp", ["First", "Second"])
    parse = SsePluginAdapter.parse

    def add_after_parse(adapter, request):
        result = parse(adapter, request)
        extra = tmp_path / "Strings" / "source_English.strings"
        extra.parent.mkdir()
        extra.write_bytes(b"\0" * 8)
        return result

    monkeypatch.setattr(SsePluginAdapter, "parse", add_after_parse)
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_SOURCE_CHANGED"):
        prepare_plugin_source(plugin)


@pytest.mark.parametrize("localized", [False, True])
@pytest.mark.parametrize("phase", ["before_write", "inside_commit"])
def test_new_strings_companion_invalidates_captured_source_set(tmp_path, monkeypatch, localized, phase):
    plugin, source, collection = _prepared(tmp_path, localized)
    original = plugin.read_bytes()
    extra = tmp_path / "Strings" / "source_English.strings"

    def create_companion():
        extra.parent.mkdir(exist_ok=True)
        extra.write_bytes(b"\0" * 8)

    if phase == "before_write":
        create_companion()
    else:
        commit = ImmediateCommitGuard.commit

        def add_before_commit(guard, run_id, mutation):
            create_companion()
            return commit(guard, run_id, mutation)

        monkeypatch.setattr(ImmediateCommitGuard, "commit", add_before_commit)
    with pytest.raises(RuntimeError, match="FOMOD_PLUGIN_SOURCE_CHANGED"):
        publish_plugin_collection(plugin, collection, source=source)
    assert plugin.read_bytes() == original
    assert extra.read_bytes() == b"\0" * 8
    assert not any(
        path.suffix in {".stage", ".bak"} or path.name.startswith(".transbridge-") for path in tmp_path.rglob("*")
    )
