"""GUI publication keeps binary EET native in single and mixed-format batches."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.ui.operations.test_strict_write_routes import (
    _context,
    _submit,
    feature as feature_fixture,
    qapp as qapp_fixture,
)
from tests.ui.test_binary_eet_sources import binary_source
from transbridge.application.contracts import OperationOutcome
from transbridge.application.io import FormatId
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.parser.eet_binary_codec import EetDocument
from transbridge.ui.eet_source import parse_eet_collection
from transbridge.ui.operations.write_dialog import WritePlanDialog

feature = feature_fixture
qapp = qapp_fixture


def _binary_slot(path):
    document = binary_source(path, duplicates=True)
    collection, parsed = parse_eet_collection(path)
    entries = tuple(collection)
    collection = TranslationEntryCollection((replace(entries[0], translation="Changed 42", stage=1), entries[1]))
    slot = SimpleNamespace(
        esp_path=None,
        eet_path=str(path),
        source_snapshot=parsed.source_snapshot,
        format_id=FormatId.BINARY_EET,
        collection=collection,
    )
    return slot, document


@pytest.mark.parametrize("remove_original", [False, True])
def test_single_gui_eet_write_preserves_complete_saved_document(tmp_path, feature, remove_original):
    path = tmp_path / "original.eet"
    slot, document = _binary_slot(path)
    context = SimpleNamespace(slots={str(path): slot}, active_slot=slot, collection=slot.collection)
    if remove_original:
        path.unlink()
        slot.eet_path = None
    output = tmp_path / "translated.eet"
    draft = feature.feature.create_draft(context, False, {"target_path": str(output)})
    assert draft.request.format_id is FormatId.BINARY_EET
    assert "实验性" in draft.mode_summary
    assert not any(item.field_id in {"plugin_path", "output_format"} for item in draft.editable_fields)
    result = _submit(feature, draft)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    actual = EetDocument.from_bytes(output.read_bytes())
    assert actual == document.with_translation(0, "Changed 42")
    if not remove_original:
        assert path.read_bytes() == document.to_bytes()


@pytest.mark.parametrize("source_suffix", [".eet", ".xml"])
def test_mixed_gui_batch_writes_native_eet_and_plugin_without_conversion(tmp_path, feature, source_suffix):
    context = _context(tmp_path)
    source = tmp_path / f"dictionary{source_suffix}"
    slot, document = _binary_slot(source)
    context.slots[str(source)] = slot
    output = tmp_path / "out"
    draft = feature.feature.create_draft(context, True, {"output_directory": str(output)})
    assert {item.format_id for item in draft.request.drafts} == {FormatId.PLUGIN_SSE, FormatId.BINARY_EET}
    assert not any(item.field_id == "output_format" for item in draft.editable_fields)
    result = _submit(feature, draft)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert {path.name for path in output.iterdir()} == {"source-0.esp", "dictionary.eet"}
    assert EetDocument.from_bytes((output / "dictionary.eet").read_bytes()) == document.with_translation(
        0, "Changed 42"
    )
    assert source.read_bytes() == document.to_bytes()


def test_native_binary_eet_save_dialog_offers_eet_filter(qapp, tmp_path, feature, monkeypatch):
    slot, _ = _binary_slot(tmp_path / "dictionary.eet")
    context = SimpleNamespace(active_slot=slot, slots={"eet": slot}, collection=slot.collection)
    draft = feature.feature.create_draft(context, False, {})
    dialog = WritePlanDialog(feature.feature.mapper.present("eet", 1, draft), context)
    filters = []

    def choose(_parent, _title, _path, file_filter, **_kwargs):
        filters.append(file_filter)
        return "", ""

    monkeypatch.setattr("transbridge.ui.operations.write_dialog.QFileDialog.getSaveFileName", choose)
    dialog._browse_output()
    assert filters == ["ESP-ESM Translator EET (*.eet)"]
    dialog.close()
