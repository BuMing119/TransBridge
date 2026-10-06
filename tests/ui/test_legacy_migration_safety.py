"""Real worker regressions for detached XML/Strings import publication."""

import struct
import threading
import time
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.application.io import legacy_migration
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.projection_types import CollectionSlot

project = source_fixtures.project
_APP = QApplication.instance() or QApplication([])


def _config(**values):
    return SimpleNamespace(**{
        "eet_path": None,
        "xt_path": None,
        "tp_path": None,
        "strings_dir": None,
        "strings_lang": "chinese",
        "strings_apply_all": False,
        **values,
    })


def _host(context):
    messages = []
    host = SimpleNamespace(
        context=context,
        workers=[],
        messages=messages,
        show_message=messages.append,
        workbench=SimpleNamespace(show_step2_progress=lambda *_: None, hide_step2_progress=lambda: None),
    )
    return host, ParseCoordinator(host)


def _finish(host):
    deadline = time.monotonic() + 5
    while (not host.messages or any(worker.isRunning() for worker in host.workers)) and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    assert host.messages
    assert all(not worker.isRunning() for worker in host.workers)


def _source(project):
    path = write_plugin(project.root / "source.esp", [(0x800, "TestNpc", "Hello")])
    result = _tool_parse_esp({"path": str(path)}, ExecutionContext(app_context=project.ctx))
    assert result.success, result.message
    return next(iter(project.ctx.collection))


def _xml(path, kind="eet"):
    content = (
        "<DocumentElement><ESP><GRUP>NPC_</GRUP><ID>00000800</ID><EDID>TestNpc</EDID>"
        "<CHAMP>FULL</CHAMP><ORIGINAL>Hello</ORIGINAL><TRADUIT>你好</TRADUIT>"
        "<INDEX>1</INDEX><STATUS>99</STATUS></ESP></DocumentElement>"
        if kind == "eet"
        else "<SSTXMLRessources><Content><String><EDID>TestNpc</EDID><REC>NPC_:FULL</REC>"
        "<Source>Hello</Source><Dest>你好</Dest></String></Content></SSTXMLRessources>"
    )
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", ["eet", "xt"])
def test_xml_import_prepares_detached_and_commits_once(project, monkeypatch, kind):
    entry = _source(project)
    source = _xml(project.root / "translated.xml", kind)
    host, coordinator = _host(project.ctx)
    slot = project.ctx.active_slot
    collection = slot.collection
    before = project.services.project_lifecycle.active.variant.snapshot()
    ready, release = threading.Event(), threading.Event()
    original_prepare = legacy_migration.prepare_legacy_migration

    def delayed(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        ready.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(legacy_migration, "prepare_legacy_migration", delayed)
    try:
        coordinator._run_migrate(slot, _config(**{f"{kind}_path": str(source)}))
        assert ready.wait(2)
        assert slot.collection is collection
        assert not collection.get(entry.identity).translation
        assert getattr(slot, f"{kind}_path") is None
        assert project.services.project_lifecycle.active.variant.snapshot() == before
    finally:
        release.set()
    _finish(host)
    after = project.services.project_lifecycle.active.variant.snapshot()
    assert after.revision == before.revision + 1
    assert project.ctx.collection.get(entry.identity).translation == "你好"
    assert getattr(slot, f"{kind}_path") == str(source)
    assert not project.ctx.authoritative_projection_diverged()


@pytest.mark.parametrize("change", ["variant", "project_edit", "direct_edit"])
def test_stale_xml_draft_never_overwrites_current_state(project, monkeypatch, change):
    entry = _source(project)
    assert project.ctx.project_commands.save(project.request).is_success
    source = _xml(project.root / "translated.xml")
    host, coordinator = _host(project.ctx)
    old_slot = project.ctx.active_slot
    ready, release = threading.Event(), threading.Event()
    original_prepare = legacy_migration.prepare_legacy_migration

    def delayed(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        ready.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(legacy_migration, "prepare_legacy_migration", delayed)
    try:
        coordinator._run_migrate(old_slot, _config(eet_path=str(source)))
        assert ready.wait(2)
        if change == "variant":
            assert project.ctx.project_commands.create_variant("Other", project.request, copy_active=True).is_success
        elif change == "project_edit":
            assert project.ctx.project_commands.replace_entry_states(
                {entry.identity: ("用户编辑", 1)}, project.ctx.runtime_context
            ).is_success
        else:
            entry.translation = "用户直接编辑"
        before = project.services.project_lifecycle.active.variant.snapshot()
        current = tuple(item.snapshot() for item in project.ctx.collection)
    finally:
        release.set()
    _finish(host)
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert tuple(item.snapshot() for item in project.ctx.collection) == current
    assert old_slot.eet_path is None
    assert any("MIGRATION_TARGET_CHANGED" in text for text in host.messages)
    assert not any("迁移完成" in text for text in host.messages)


def test_failed_second_xml_source_does_not_publish_first_source(project):
    entry = _source(project)
    good = _xml(project.root / "translated.xml")
    bad = project.root / "bad.xml"
    bad.write_text("<broken", encoding="utf-8")
    host, coordinator = _host(project.ctx)
    slot = project.ctx.active_slot
    before = project.services.project_lifecycle.active.variant.snapshot()

    coordinator._run_migrate(slot, _config(eet_path=str(good), xt_path=str(bad)))
    _finish(host)

    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert not project.ctx.collection.get(entry.identity).translation
    assert slot.eet_path is None and slot.xt_path is None
    assert any("MIGRATION_LEGACY_SOURCE_INVALID" in text for text in host.messages)


@pytest.mark.parametrize("failure", ["rejected", "exception"])
def test_rejected_xml_commit_leaves_projection_and_paths_unchanged(project, monkeypatch, failure):
    entry = _source(project)
    source = _xml(project.root / "translated.xml")
    host, coordinator = _host(project.ctx)
    slot = project.ctx.active_slot
    collection = slot.collection
    before = project.services.project_lifecycle.active.variant.snapshot()

    def reject(*args, **kwargs):
        if failure == "exception":
            raise OSError("commit unavailable")
        return SimpleNamespace(
            is_success=False,
            diagnostics=[SimpleNamespace(code="COMMIT_REJECTED", message="commit unavailable")],
        )

    monkeypatch.setattr(project.ctx.project_commands, "replace_entry_states", reject)
    coordinator._run_migrate(slot, _config(eet_path=str(source)))
    _finish(host)

    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert slot.collection is collection
    assert not collection.get(entry.identity).translation
    assert slot.eet_path is None
    assert any("迁移失败" in text and "commit unavailable" in text for text in host.messages)


def test_strings_apply_all_rejects_whole_draft_if_one_file_is_invalid(tmp_path):
    def slot(stem):
        entry = TranslationEntry(stem, stem, "Hello", "", 0, "NPC_:FULL", string_id=7)
        return CollectionSlot(stem, TranslationEntryCollection([entry]), esp_path=f"{stem}.esp")

    first, second = slot("First"), slot("Second")
    text = "你好".encode() + b"\0"
    (tmp_path / "First_Chinese.strings").write_bytes(struct.pack("<IIII", 1, len(text), 7, 0) + text)
    (tmp_path / "Second_Chinese.strings").write_bytes(b"bad")
    context = SimpleNamespace(
        active_slot=first,
        slots={"first": first, "second": second},
        uses_authoritative_projection=False,
        runtime_context=None,
        collection_changed=SimpleNamespace(emit=lambda *_: None),
    )
    host, coordinator = _host(context)
    coordinator._run_migrate(first, _config(strings_dir=str(tmp_path), strings_apply_all=True))
    _finish(host)

    assert not next(iter(first.collection)).translation
    assert not next(iter(second.collection)).translation
    assert first.strings_lookup is None and first.strings_path is None
    assert any("MIGRATION_STRINGS_INVALID" in text for text in host.messages)


def test_successful_strings_lookup_is_published_even_without_new_translation(tmp_path):
    entry = TranslationEntry("one", "one", "Hello", "人工译文", 3, "NPC_:FULL", string_id=7)
    slot = CollectionSlot("Plugin", TranslationEntryCollection([entry]), esp_path="Plugin.esp")
    text = "导入译文".encode() + b"\0"
    (tmp_path / "Plugin_Chinese.strings").write_bytes(struct.pack("<IIII", 1, len(text), 7, 0) + text)
    context = SimpleNamespace(
        active_slot=slot,
        slots={"plugin": slot},
        uses_authoritative_projection=False,
        runtime_context=None,
        collection_changed=SimpleNamespace(emit=lambda *_: None),
    )
    original_collection = slot.collection
    host, coordinator = _host(context)

    coordinator._run_migrate(slot, _config(strings_dir=str(tmp_path)))
    _finish(host)

    assert slot.collection is original_collection
    assert entry.translation == "人工译文" and entry.stage == 3
    assert slot.strings_lookup.get(7) == "导入译文"
    assert slot.strings_path is None
    assert any("新增 0 条译文" in text for text in host.messages)


def test_identical_xml_translation_registers_source_without_redundant_commit(project):
    entry = _source(project)
    assert project.ctx.project_commands.replace_entry_states(
        {entry.identity: ("你好", 1)}, project.ctx.runtime_context
    ).is_success
    # Refresh the facade after the authoritative edit, as the production UI does.
    project.ctx.collection.get(entry.identity).translation = "你好"
    project.ctx.collection.get(entry.identity).stage = 1
    source = _xml(project.root / "translated.xml")
    slot = project.ctx.active_slot
    before = project.services.project_lifecycle.active.variant.snapshot()
    original_collection = slot.collection
    host, coordinator = _host(project.ctx)

    coordinator._run_migrate(slot, _config(eet_path=str(source)))
    _finish(host)

    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert slot.collection is original_collection
    assert slot.eet_path == str(source)
