"""EET create/append/write workflows use saved sources and project identities."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.eet_fixtures import eet_record, write_eet
from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools.test_safe_write_back import manager as manager_fixture
from tests.smart_assistant.tools.test_source_import_authority import project as project_fixture
from tests.smart_assistant.tools.test_write_back_publication_boundary import _finish
from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.io.eet_source import parse_eet_source
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.smart_assistant.tools import _source_import, tool_writer
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_eet, _tool_parse_esp
from transbridge.smart_assistant.tools.translation_task_results import load_task_result
from transbridge.smart_assistant.tools.types import ExecutionContext

project = project_fixture
manager = manager_fixture


def _load(project, kind, duplicate=False):
    ctx = ExecutionContext(app_context=project.ctx)
    if kind == "plugin":
        path = write_plugin(project.root / "base.esp", [(0x800, "Greeting", "Hello")])
        result = _tool_parse_esp({"path": str(path)}, ctx)
    else:
        records = [eet_record(translation="")]
        if duplicate:
            records.append(eet_record(translation="", index=2))
        path = write_eet(project.root / "base.eet", records)
        result = _tool_parse_eet({"path": str(path)}, ctx)
    assert result.success, result.message
    return ctx, tuple(project.ctx.collection)


@pytest.mark.parametrize("kind", ["plugin", "eet"])
def test_eet_append_commits_target_identity_and_captures_evidence(project, kind):
    ctx, before = _load(project, kind)
    path = write_eet(project.root / "translated.eet")
    result = _tool_parse_eet({"path": str(path), "action": "append"}, ctx)
    assert result.success, result.message
    after = next(iter(project.ctx.collection))
    assert after.identity == before[0].identity
    assert after.metadata == before[0].metadata
    assert after.translation == "你好"
    assert project.services.project_lifecycle.active.variant.snapshot().entries[0].translation == "你好"
    assert not project.ctx.authoritative_projection_diverged()
    _, record, _ = load_task_result(ctx, result.data["result_id"])
    assert record["input_snapshot"] and record["raw_entries"]


def test_binary_eet_source_is_registered_with_native_snapshot(project):
    _load(project, "eet", duplicate=True)
    slot = project.ctx.active_slot
    assert slot.format_id is FormatId.BINARY_EET
    assert slot.eet_path.endswith("base.eet")
    assert len(slot.collection) == 2
    assert slot.source_snapshot.content.startswith(b"EET_")
    assert project.services.gui_project_commands.save(project.request).is_success


@pytest.mark.parametrize(
    "reason", ["source_duplicate", "target_duplicate", "original", "field", "form_id", "editor_id"]
)
def test_eet_append_skips_ambiguous_or_unmatched_records(project, reason):
    ctx, before = _load(project, "eet", duplicate=reason == "target_duplicate")
    changes = {"original": {"original": "Other"}, "field": {"field": "DESC"}, "form_id": {"form_id": "00000900"}}
    changes["editor_id"] = {"editor_id": "OtherNpc"}
    records = [eet_record(**changes.get(reason, {}))]
    if reason == "source_duplicate":
        records.append(eet_record(index=2))
    path = write_eet(project.root / "translated.eet", records)
    result = _tool_parse_eet({"path": str(path), "action": "append"}, ctx)
    assert not result.success and result.partial
    assert result.data["added_count"] == 0
    assert tuple(project.ctx.collection) == before


def test_eet_append_uses_captured_content_when_source_disappears(project, monkeypatch):
    ctx, _ = _load(project, "plugin")
    path = write_eet(project.root / "translated.eet")
    publish = _source_import.publish_import

    def remove_then_publish(*args, **kwargs):
        path.unlink()
        return publish(*args, **kwargs)

    monkeypatch.setattr(_source_import, "publish_import", remove_then_publish)
    result = _tool_parse_eet({"path": str(path), "action": "append"}, ctx)
    assert result.success, result.message
    assert next(iter(project.ctx.collection)).translation == "你好"


def test_eet_append_rejects_entries_tampered_after_parse(project, monkeypatch):
    ctx, before = _load(project, "eet")
    path = write_eet(project.root / "translated.eet")
    publish = _source_import.publish_import

    def changed(target, path, label, collection, *args, **kwargs):
        injected = (replace(next(iter(collection)), translation="Injected"),)
        return publish(target, path, label, injected, *args, **kwargs)

    monkeypatch.setattr(_source_import, "publish_import", changed)
    result = _tool_parse_eet({"path": str(path), "action": "append"}, ctx)
    assert not result.success and "EET_IMPORT_SNAPSHOT_MISMATCH" in result.message
    assert tuple(project.ctx.collection) == before


@pytest.mark.parametrize("target", ["eet", "auto"])
@pytest.mark.parametrize("suffix", [".eet", ".xml"])
def test_assistant_eet_write_uses_snapshot_and_rebuilds_numeric_cache(tmp_path, manager, target, suffix):
    source = write_eet(tmp_path / ("source" + suffix), [eet_record(original="Value 50", translation="数值50")])
    parsed = parse_eet_source(source)
    entry = replace(parsed.entries[0], translation="数值75", stage=1)
    slot = SimpleNamespace(
        source_snapshot=parsed.source_snapshot,
        collection=TranslationEntryCollection([entry]),
        format_id=FormatId.BINARY_EET,
        eet_path=None,
        label="EET",
    )
    ctx = SimpleNamespace(
        slots={"eet": slot},
        active_slot=slot,
        active_key="eet",
        request_context=RequestContext("eet-writer", authorized_roots=(str(tmp_path),)),
    )
    source.unlink()
    output = tmp_path / "out"
    args = {"target": target, "output_dir": str(output)}
    if target == "auto":
        args["source_keys"] = ["eet"]
    started = tool_writer._tool_write_back(args, ctx)
    result = _finish(manager, started, "completed").result
    assert result["artifacts"] == [str(output / "source.eet")]
    actual = EetDocument.from_bytes((output / "source.eet").read_bytes())
    assert actual.records[0].translation == "数值75"
    assert actual.records[0].translation_number_cache.values == ("75",)
    assert actual.records[0].status == 99
