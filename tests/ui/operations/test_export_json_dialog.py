from dataclasses import replace
import json
from pathlib import Path
import time
from types import SimpleNamespace

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox
import pytest

from tests.ui.operations.test_strict_write_routes import (
    _context,
    _submit,
    feature as feature_fixture,
    qapp as qapp_fixture,
)
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.contracts import FormatId
from transbridge.application.io.json_export import JsonExportDraft, JsonExportWorkload, preflight_json_export
from transbridge.application.io.json_source import parse_json_source
from transbridge.application.io.operation_write import HydratedWritePreflightService
from transbridge.application.io.paratranz_mapping import paratranz_record_from_entry
from transbridge.application.io.publish.guards import ImmediateCommitGuard
from transbridge.application.tasks import OwnerRef
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.production import build_operation_plan_facade

feature = feature_fixture
qapp = qapp_fixture


@pytest.mark.parametrize("format_id", ["json.paratranz", "json.dsd"])
@pytest.mark.parametrize("batch", [False, True])
def test_explicit_json_export_uses_distinct_structures_and_preserves_sources(tmp_path, feature, format_id, batch):
    context = _context(tmp_path, 2 if batch else 1)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    values = {"output_format": format_id}
    values.update(
        {"output_directory": str(tmp_path / "exports")} if batch else {"target_path": str(tmp_path / "out.json")}
    )
    draft = feature.feature.create_draft(context, batch, values)
    assert isinstance(draft.request, JsonExportDraft)
    result = _submit(feature, draft)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert len(result.artifact_refs) == len(context.slots)
    for target in result.artifact_refs:
        rows = json.loads(Path(target).read_text(encoding="utf-8"))
        assert len(rows) == 2
        if format_id == "json.dsd":
            assert set(rows[0]) == {"form_id", "type", "string"}
            assert rows[0]["form_id"] and rows[0]["type"] == "NPC_ FULL"
            assert rows[0]["string"] == "译文0"
        else:
            assert {"key", "original", "translation", "stage"} <= rows[0].keys()
            assert rows[0]["translation"] == "译文0"
            assert (
                rows[0]["key"] == paratranz_record_from_entry(next(iter(context.active_slot.collection)))["key"]
                or batch
            )
    assert all(Path(path).read_bytes() == data for path, data in originals.items())


def test_json_export_rejects_existing_target_without_confirmation_and_changed_target(tmp_path, feature):
    context = _context(tmp_path)
    target = tmp_path / "out.json"
    target.write_text("old", encoding="utf-8")
    draft = feature.feature.create_draft(
        context, False, {"output_format": "json.paratranz", "target_path": str(target)}
    )
    assert not feature.feature.mapper.preflight(draft).ready
    draft = feature.feature.edit_draft(draft, (("overwrite_confirmed", "true"),))
    checked = feature.feature.mapper.preflight(draft)
    assert checked.ready
    target.write_text("changed", encoding="utf-8")
    ref = feature.feature.submit(draft, checked, feature.owner, feature.tasks)
    result = feature.adapter.result(ref, feature.owner)
    assert result.outcome is OperationOutcome.FAILED
    assert target.read_text() == "changed"


def test_json_export_can_return_to_native_without_converting_source_identity(tmp_path, feature):
    context = _context(tmp_path)
    native = feature.feature.create_draft(context, False, {})
    exported = feature.feature.edit_draft(
        native, (("output_format", "json.paratranz"), ("target_path", str(tmp_path / "x.json")))
    )
    restored = feature.feature.edit_draft(
        exported, (("output_format", "plugin.sse"), ("target_path", str(tmp_path / "x.esp")))
    )
    assert restored.request.format_id is FormatId.PLUGIN_SSE
    assert _submit(feature, restored).outcome is OperationOutcome.COMPLETED


def test_dsd_unknown_plugin_identity_is_rejected(tmp_path, feature):
    from tests.ui.operations.test_write_binary_eet import _binary_slot

    context = _context(tmp_path)
    slot, _ = _binary_slot(tmp_path / "dictionary.eet")
    context.active_slot = slot
    context.slots = {"eet": slot}
    draft = feature.feature.create_draft(
        context, False, {"output_format": "json.dsd", "target_path": str(tmp_path / "out.json")}
    )
    checked = feature.feature.mapper.preflight(draft)
    assert not checked.ready
    assert any(item.check_id == "DSD_LOCATOR_REQUIRED" for item in checked.checks)


@pytest.mark.parametrize("wrapper", [False, True])
def test_native_dsd_export_restores_hydrated_locators_without_losing_distinct_originals(tmp_path, feature, wrapper):
    source = tmp_path / "source.json"
    records = [
        {"form_id": "00000800|Source.esp", "type": "QUST CNAM", "original": original, "string": translation}
        for original, translation in (("First", "第一"), ("Second", "第二"))
    ]
    source.write_text(json.dumps({"entries": records} if wrapper else records), encoding="utf-8")
    parsed = parse_json_source(str(source), {"format": "dsd"})
    slot = SimpleNamespace(source_snapshot=parsed.snapshot, format_id=parsed.format_id, collection=parsed.collection)
    context = SimpleNamespace(active_slot=slot, slots={str(source): slot}, collection=slot.collection)
    output = tmp_path / "export.json"
    draft = feature.feature.create_draft(context, False, {"output_format": "json.dsd", "target_path": str(output)})
    source.unlink()  # Saved source bytes are authoritative, even when the original file disappears.
    assert _submit(feature, draft).outcome is OperationOutcome.COMPLETED
    assert json.loads(output.read_text(encoding="utf-8")) == records


def test_dsd_plugin_export_rejects_changed_original_instead_of_guessing_locator(tmp_path, feature):
    context = _context(tmp_path)
    first, second = context.active_slot.collection
    context.active_slot.collection = TranslationEntryCollection((replace(first, original="Mismatch"), second))
    draft = feature.feature.create_draft(
        context, False, {"output_format": "json.dsd", "target_path": str(tmp_path / "out.json")}
    )
    checked = feature.feature.mapper.preflight(draft)
    assert not checked.ready
    assert any(item.check_id == "DSD_LOCATOR_REQUIRED" for item in checked.checks)
    assert not (tmp_path / "out.json").exists()


@pytest.mark.parametrize("format_id", ["json.paratranz", "json.dsd"])
def test_cancelled_batch_json_export_does_not_publish_any_file(tmp_path, feature, format_id):
    draft = feature.feature.create_draft(
        _context(tmp_path, 2), True, {"output_format": format_id, "output_directory": str(tmp_path / "exports")}
    )
    checked = preflight_json_export(draft.request, HydratedWritePreflightService())
    result = JsonExportWorkload(checked)(
        SimpleNamespace(
            ref=SimpleNamespace(run_id="cancelled-export"),
            cancellation=SimpleNamespace(is_cancelled=True),
            publish_commit_guard=lambda: ImmediateCommitGuard("cancelled-export"),
        )
    )
    assert result.outcome is OperationOutcome.CANCELLED
    assert result.counts.cancelled == 2
    assert not result.artifact_refs
    assert not list((tmp_path / "exports").glob("*.json"))


def test_json_batch_reports_partial_and_preserves_externally_changed_target(tmp_path, feature):
    draft = feature.feature.create_draft(
        _context(tmp_path, 2),
        True,
        {"output_format": "json.paratranz", "output_directory": str(tmp_path / "exports")},
    )
    checked = feature.feature.mapper.preflight(draft)
    assert checked.ready
    changed = Path(draft.request.sources[1].target_path)
    changed.parent.mkdir()
    changed.write_text("external change")
    ref = feature.feature.submit(draft, checked, feature.owner, feature.tasks)
    result = feature.adapter.result(ref, feature.owner)
    assert result.outcome is OperationOutcome.PARTIAL
    assert result.counts.succeeded == result.counts.failed == 1
    assert changed.read_text() == "external change"
    assert len(result.artifact_refs) == 1
    assert json.loads(Path(result.artifact_refs[0]).read_text(encoding="utf-8"))


@pytest.mark.parametrize("format_id", ["plugin.sse", "json.paratranz", "json.dsd"])
def test_export_button_runs_checks_and_writes_without_manual_preflight(tmp_path, feature, qapp, format_id):
    context = _context(tmp_path)
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    target = tmp_path / ("out.esp" if format_id == "plugin.sse" else "out.json")
    dialog = facade.begin_write(context, output_format=format_id, target_path=str(target))
    try:
        assert dialog.windowTitle() == "导出文件"
        assert dialog._preflight_button.isHidden()
        assert dialog._edit_button.isHidden()
        assert dialog._checks_scroll.isHidden()
        assert dialog._summary_scroll.isHidden()
        assert dialog._overwrite.isHidden()
        dialog._confirm_button.click()
        _finish_export(dialog, qapp)
        assert target.exists(), dialog._checks.text()
        assert dialog._checks.property("tbStatusId") == "completed"
    finally:
        dialog.close()


@pytest.mark.parametrize("format_id,suffix", [("json.paratranz", ".json"), ("plugin.sse", ".esp")])
def test_automatic_export_requests_overwrite_only_for_existing_file(
    tmp_path, feature, qapp, monkeypatch, format_id, suffix
):
    context = _context(tmp_path)
    target = tmp_path / f"out{suffix}"
    target.write_text("existing")
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    dialog = facade.begin_write(context, output_format=format_id, target_path=str(target))
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: prompts.append(a) or QMessageBox.StandardButton.Yes)
    try:
        dialog._confirm_button.click()
        _finish_export(dialog, qapp)
        assert len(prompts) == 1
        if format_id == "json.paratranz":
            assert isinstance(json.loads(target.read_text(encoding="utf-8")), list)
        else:
            assert target.read_bytes().startswith(b"TES4")
    finally:
        dialog.close()


def test_declining_automatic_overwrite_preserves_file_and_starts_no_write(tmp_path, feature, qapp, monkeypatch):
    target = tmp_path / "existing.esp"
    target.write_bytes(b"untouched existing file")
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    dialog = facade.begin_write(_context(tmp_path), target_path=str(target))
    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: prompts.append(a) or QMessageBox.StandardButton.No)
    try:
        dialog._confirm_button.click()
        deadline = time.monotonic() + 5
        while not prompts and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert len(prompts) == 1
        assert target.read_bytes() == b"untouched existing file"
        assert not dialog._execution_running
        assert not feature.tasks.list(OwnerRef("gui", "gui.operation-plan"))
    finally:
        dialog.close()


def _finish_export(dialog, qapp):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and dialog._checks.property("tbStatusId") != "completed":
        qapp.processEvents()
        QTest.qWait(10)
    assert dialog._checks.property("tbStatusId") == "completed", dialog._checks.text()


@pytest.mark.parametrize("accept", [False, True])
def test_save_original_eet_requires_confirmation_and_preserves_native_format(
    tmp_path, feature, qapp, monkeypatch, accept
):
    from tests.ui.operations.test_write_binary_eet import _binary_slot
    from transbridge.parser.eet_binary_codec import EetDocument

    path = tmp_path / "original.eet"
    slot, document = _binary_slot(path)
    before = path.read_bytes()
    context = SimpleNamespace(active_slot=slot, slots={str(path): slot}, collection=slot.collection)
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    dialog = facade.begin_write(context)
    prompts = []
    answer = QMessageBox.StandardButton.Yes if accept else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", lambda *args: prompts.append(args) or answer)
    try:
        dialog._save_original_button.click()
        assert dialog._field_edits["target_path"].text() == str(path)
        assert path.read_bytes() == before
        assert not dialog._overwrite.isChecked()
        dialog._confirm_button.click()
        if accept:
            _finish_export(dialog, qapp)
            assert EetDocument.from_bytes(path.read_bytes()) == document.with_translation(0, "Changed 42")
        else:
            deadline = time.monotonic() + 5
            while not prompts and time.monotonic() < deadline:
                qapp.processEvents()
                QTest.qWait(10)
            assert path.read_bytes() == before
            assert not feature.tasks.list(OwnerRef("gui", "gui.operation-plan"))
        assert len(prompts) == 1
    finally:
        dialog.close()
