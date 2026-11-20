"""Saved navigation facts survive a new service and real paged editor activation."""

import json
import sqlite3
import struct

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
import pytest

from tests.dialogue_catalog_support import field, plugin_bytes, record, reference
from tests.integration.gui import test_project_paged_workbench as paged
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.persistence.project_store.paths import project_path
from transbridge.persistence.project_store.repository import ProjectStore
from transbridge.ui.dialogue.controller import DialogueEditorController
from transbridge.ui.project_rows import ProjectRowsCollection
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL

qapp = paged.qapp


def _plugin():
    def responses(topic, info, text):
        content = record(
            "INFO", info, field("TRDT", bytes(12) + b"\1" + bytes(11)) + field("NAM1", text.encode() + b"\0")
        )
        return struct.pack("<4sIIiHHHH", b"GRUP", len(content) + 24, topic, 7, 0, 0, 0, 0) + content

    return plugin_bytes({
        "QUST": record("QUST", 1, field("EDID", b"SavedQuest\0")),
        "DIAL": (
            record("DIAL", 0x10, field("EDID", b"NamedTopic\0") + reference("QNAM", 1) + field("FULL", b"Prompt\0"))
            + responses(0x10, 0x20, "Spoken response")
            + record("DIAL", 0x11, field("EDID", b"EmptyTopic\0") + reference("QNAM", 1))
            + record("DIAL", 0x30, field("EDID", b"OtherTopic\0") + reference("QNAM", 1))
            + responses(0x30, 0x31, "Unrelated response")
        ),
        "SCEN": (
            record(
                "SCEN",
                0x40,
                field("EDID", b"NamedScene\0")
                + reference("PNAM", 1)
                + field("ANAM", b"\0\0")
                + reference("DATA", 0x10)
                + field("ANAM", b"")
                + field("ANAM", b"\0\0")
                + reference("DATA", 0x11)
                + field("ANAM", b""),
            )
            + record("SCEN", 0x41, field("EDID", b"EmptyScene\0") + reference("PNAM", 1))
        ),
    })


def _legacy_same_bytes_update(root, database, source):
    # Model an older project that saved order evidence before catalogs existed.
    with sqlite3.connect(database) as connection:
        (serialized,) = connection.execute("SELECT format_fields FROM source").fetchone()
        fields = json.loads(serialized)
        assert fields.pop("plugin_dialogue_catalog")
        connection.execute("UPDATE source SET format_fields = ?", (json.dumps(fields),))
    services = paged._services(root)
    try:
        updates = services.project_source_updates
        targets = updates.list_sources(str(database), paged._CONTEXT)
        assert targets.is_success, targets.diagnostics
        (target,) = targets.value
        prepared = updates.prepare(str(database), target.source_id, str(source), paged._CONTEXT)
        assert prepared.is_success, prepared.diagnostics
        assert prepared.value.changed == prepared.value.added == prepared.value.removed == 0
        committed = updates.commit(prepared.value.token, paged._CONTEXT)
        assert committed.is_success, committed.diagnostics
    finally:
        services.close()
    assert "plugin_dialogue_catalog" in dict(ProjectStore(database).sources()[0].format_fields)


@pytest.mark.parametrize("legacy_update", [False, True], ids=["new-project", "legacy-same-bytes-update"])
def test_project_catalog_reopens_after_source_removal_and_double_click(tmp_path, qapp, legacy_update):
    root = tmp_path / "data"
    source = tmp_path / "Dialogue.esp"
    original_bytes = _plugin()
    source.write_bytes(original_bytes)
    setup = paged._services(root)
    try:
        created = setup.gui_project_commands.create_project(
            ProjectProvisioningRequest("Saved scene navigation", source=ProjectSourceRequest(str(source))),
            paged._CONTEXT,
        )
        assert created.is_success, created.diagnostics
        active = setup.project_lifecycle.active
        database = project_path(root, active.project_ref.identity)
        variant = active.formal_variant_ref.identity
        rows = tuple(ProjectStore(database).iter_entries(variant))
        response = next(row for row in rows if row.snapshot.original == "Spoken response")
        edited = setup.gui_project_commands.update_entry(
            response.state.entry_key, paged._CONTEXT, translation="Saved translation", stage=3
        )
        assert edited.is_success, edited.diagnostics
        saved = setup.gui_project_commands.save(paged._CONTEXT)
        assert saved.is_success, saved.diagnostics
    finally:
        setup.close()

    if legacy_update:
        _legacy_same_bytes_update(root, database, source)
    assert source.read_bytes() == original_bytes
    expected = tuple(ProjectStore(database).iter_entries(variant))
    source.unlink()

    # A new composition must recover from durable facts, with no parser or input.
    with paged._workbench(root, qapp) as (host, coordinator, services):
        failures = paged._begin_open(host, coordinator, database)
        paged._wait_until(qapp, lambda: host.project_open_worker is None)
        assert not failures, failures
        assert isinstance(host.context.collection, ProjectRowsCollection)
        assert host.context.active_slot.plugin is None
        assert host.context.active_slot.source_snapshot.content is None
        preview = host.workbench.preview
        paged._wait_until(qapp, lambda: preview._paging.resident_ready)
        editor = DialogueEditorController(
            host.context, host.workbench, preview, host.workers, projection=services.project_projection
        )
        try:
            table = preview._table
            row = next(
                row
                for row in range(table.model().rowCount())
                if table.entry_at_row(row).identity == response.state.entry_key
            )
            table.scrollTo(table.model().index(row, COL_ORIGINAL))
            qapp.processEvents()
            point = table.visualRect(table.model().index(row, COL_ORIGINAL)).center()
            QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            paged._wait_until(qapp, lambda: editor.dialog.isVisible() and editor._delivery is None)
            assert editor._selected_key == response.state.entry_key
            assert editor.view.translation.toPlainText() == "Saved translation"
            assert editor.view.context_panel.isVisible()
            (quest,) = editor._index.quests
            assert quest.label == "QUST {SavedQuest} [00000001]"
            topics = {topic.identity[2]: topic for topic in quest.topics}
            assert topics["00000010"].label == "DIAL {NamedTopic} [00000010]"
            assert topics["00000011"].label == "DIAL {EmptyTopic} [00000011]"
            assert topics["00000011"].entries == ()
            scene = topics["SCEN:00000040"]
            assert scene.label == "SCEN {NamedScene} [00000040]"
            assert scene.entries == topics["00000010"].entries
            assert {host.context.collection.get(key).original for key in scene.entries} == {"Prompt", "Spoken response"}
            assert topics["SCEN:00000041"].label == "SCEN {EmptyScene} [00000041]"
            assert topics["SCEN:00000041"].entries == ()
            for identity in ("SCEN:00000040", "SCEN:00000041"):
                scene_row = next(index for index, topic in enumerate(quest.topics) if topic.identity[2] == identity)
                index = editor.view.tree_model.index(scene_row, 0)
                tree = editor.view.tree
                QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=tree.visualRect(index).center())
                assert editor.view.table_model.rowCount() == len(topics[identity].entries)
            assert not host.context.dirty
        finally:
            editor.close()
            paged._wait_until(qapp, lambda: editor._delivery is None)

    assert tuple(ProjectStore(database).iter_entries(variant)) == expected
