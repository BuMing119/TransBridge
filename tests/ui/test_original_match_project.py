"""Original-qualified entries survive authoritative edits and disk persistence."""

from uuid import uuid4

from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.entry_projection import EntryProjectionUpdate

project = source_fixtures.project


def test_same_key_entries_edit_save_and_reopen_independently(project):
    source = write_plugin(
        project.root / "same-key.esp",
        [(0x800, "Same", "First"), (0x800, "Same", "Second"), (0x801, "Other", "Ordinary")],
    )
    loaded = _tool_parse_esp({"path": str(source)}, ExecutionContext(app_context=project.ctx))
    assert loaded.success, loaded.message
    entries = list(project.ctx.collection)
    first, second, ordinary = entries
    assert len(entries) == 3 and first.key == second.key and first.id == second.id
    assert first.requires_original_match and second.requires_original_match
    assert not ordinary.requires_original_match
    assert project.ctx.project_commands.replace_entry_states(
        {first.identity: ("第一条", 1), second.identity: ("第二条", 3)}, project.request
    ).is_success
    project.ctx.active_slot.collection = EntryProjectionUpdate.from_snapshot(
        project.services.project_projection.snapshot()
    ).collection(project.ctx.collection)
    assert project.ctx.collection.get(first.identity).translation == "第一条"
    assert project.ctx.collection.get(second.identity).translation == "第二条"
    assert not project.ctx.authoritative_projection_diverged()
    assert project.ctx.project_commands.save(project.request).is_success

    restarted = build_persistence_v2_services(
        project.root / "project", id_factory=lambda: uuid4().hex, timestamp_factory=lambda: "now"
    )
    try:
        prepared = restarted.current_project_opener.prepare_active(project.request)
        assert prepared.is_success, prepared.diagnostics
        assert restarted.current_project_opener.activate(prepared.value, project.request).is_success
        restored = {entry.entry_key: entry for entry in restarted.project_lifecycle.active.variant.snapshot().entries}
        assert restored[first.identity].translation == "第一条"
        assert restored[second.identity].translation == "第二条"
        assert len(restored) == 3
    finally:
        restarted.close()
