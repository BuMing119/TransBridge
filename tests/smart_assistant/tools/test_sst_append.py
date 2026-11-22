"""SST migration uses the desktop's locator and existing-translation rules."""

from dataclasses import replace

import pytest

from tests.contracts.io.test_sst_adapter import _record
from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools.test_source_import_authority import project as project_fixture
from transbridge.parser.xt.sst_codec import SstDocument
from transbridge.smart_assistant.tools import _source_import
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp, _tool_parse_sst
from transbridge.smart_assistant.tools.types import ExecutionContext

project = project_fixture


def _sst(project, name, records, magic=b"SSU9"):
    path = project.root / name
    masters = ("example.esp",) if magic == b"SSU9" else ()
    path.write_bytes(SstDocument(magic, 0, masters, (), tuple(records)).to_bytes())
    return path


def _load(project, kind, *, duplicate=False, magic=b"SSU9"):
    ctx = ExecutionContext(app_context=project.ctx)
    if kind == "plugin":
        path = write_plugin(project.root / "original.esp", [(0x800, "Greeting", "Hello")])
        result = _tool_parse_esp({"path": str(path)}, ctx)
    else:
        records = [_record(translation="")]
        if duplicate:
            records.append(_record(translation="", index=1))
        path = _sst(project, "original.sst", records, magic)
        result = _tool_parse_sst({"path": str(path)}, ctx)
    assert result.success, result.message
    return ctx, tuple(project.ctx.collection)


@pytest.mark.parametrize("kind", ["plugin", "sst"])
@pytest.mark.parametrize("magic", [b"SSU8", b"SSU9"])
def test_append_maps_translated_sst_to_authoritative_target_identity(project, kind, magic):
    ctx, before = _load(project, kind, magic=magic)
    path = _sst(project, "translated.sst", [_record(translation="Imported")], magic)
    result = _tool_parse_sst({"path": str(path), "action": "append"}, ctx)
    assert result.success, result.message
    assert result.data["added_count"] == 1
    after = next(iter(project.ctx.collection))
    assert after.identity == before[0].identity
    assert after.metadata == before[0].metadata and after.string_id == before[0].string_id
    assert after.translation == "Imported"
    assert project.services.project_lifecycle.active.variant.snapshot().entries[0].translation == "Imported"
    assert not project.ctx.authoritative_projection_diverged()
    assert "result_id" not in result.data


@pytest.mark.parametrize("reason", ["original", "type", "form_id", "duplicate_source", "duplicate_target"])
def test_append_never_guesses_unmatched_or_ambiguous_records(project, reason):
    ctx, before = _load(project, "sst", duplicate=reason == "duplicate_target")
    record = _record(translation="Imported")
    changes = {"original": {"original": "Other"}, "type": {"field_type": "DESC"}, "form_id": {"form_id": 0x900}}
    records = [replace(record, **changes.get(reason, {}))]
    if reason == "duplicate_source":
        records.append(replace(record, index=1))
    path = _sst(project, "translated.sst", records)
    result = _tool_parse_sst({"path": str(path), "action": "append"}, ctx)
    assert result.success and result.data["added_count"] == 0, result.message
    assert tuple(project.ctx.collection) == before


def test_append_keeps_captured_snapshot_when_input_disappears(project, monkeypatch):
    ctx, _ = _load(project, "plugin")
    path = _sst(project, "translated.sst", [_record(translation="Captured")])
    prepare = _source_import.prepare_translation_import

    def remove_then_publish(*args, **kwargs):
        draft = prepare(*args, **kwargs)
        path.unlink()
        return draft

    monkeypatch.setattr(_source_import, "prepare_translation_import", remove_then_publish)
    result = _tool_parse_sst({"path": str(path), "action": "append"}, ctx)
    assert result.success, result.message
    assert next(iter(project.ctx.collection)).translation == "Captured"


@pytest.mark.parametrize("signature", ["AB C", "AB:C", "????"])
def test_legal_unknown_sst_signatures_are_retained_unmapped(project, signature):
    ctx, before = _load(project, "plugin")
    path = _sst(project, "unknown.sst", [_record(record_type=signature)])
    result = _tool_parse_sst({"path": str(path), "action": "append"}, ctx)
    assert result.success and result.data["added_count"] == 0, result.message
    assert tuple(project.ctx.collection) == before


def test_translated_and_empty_duplicate_source_records_are_ambiguous(project):
    ctx, before = _load(project, "plugin")
    path = _sst(project, "duplicate.sst", [_record(), _record(index=1, translation="")])
    result = _tool_parse_sst({"path": str(path), "action": "append"}, ctx)
    assert result.success and result.data["added_count"] == 0, result.message
    assert tuple(project.ctx.collection) == before


def test_empty_sst_translations_cannot_clear_current_translation(project):
    ctx, before = _load(project, "plugin")
    path = _sst(project, "empty.sst", [_record(translation="")])
    result = _tool_parse_sst({"path": str(path), "action": "append"}, ctx)
    assert not result.success and "MIGRATION_SST_NO_TRANSLATIONS" in result.message
    assert tuple(project.ctx.collection) == before
