"""Read DSD locators recorded by projects before native field presence was stored."""

import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.dsd_projection import DSD_LOCATOR_METADATA, restore_extracted_dsd_locator
from transbridge.application.io.json_export import JsonExportDraft, preflight_json_export
from transbridge.application.io.operation_write import HydratedWriteDraft, HydratedWritePreflightService
from transbridge.converter.translation_entry import TranslationEntry


def _saved(field, native, saved, *, original=""):
    key = f"Editor:01000800|{native}~{field.replace(' ', ':')}"
    return TranslationEntry(
        key,
        key,
        original,
        "Translation",
        1,
        field,
        metadata=((DSD_LOCATOR_METADATA, ("01000800|Example.esp", field, saved, "Editor")),),
    )


@pytest.mark.parametrize(
    ("field", "native", "saved", "expected"),
    [
        ("MESG ITXT", 0, 1, 0),
        ("MESG ITXT", 1, 1, 1),
        ("QUST NNAM", -32768, -32768, 32768),
    ],
)
def test_old_plugin_uses_recorded_index(field, native, saved, expected):
    entry = restore_extracted_dsd_locator(_saved(field, native, saved), source_format=FormatId.PLUGIN_SSE)
    assert entry.to_dsd_dict()["index"] == expected


def test_old_dsd_journal_uses_demonstrably_explicit_index():
    entry = restore_extracted_dsd_locator(_saved("QUST CNAM", 17, 17), source_format=FormatId.JSON_DSD)
    record = entry.to_dsd_dict()
    assert record["index"] == 17
    assert "original" not in record


def test_old_plugin_journal_retains_legacy_original_without_physical_log_index():
    entry = restore_extracted_dsd_locator(
        _saved("QUST CNAM", 1654, 1654, original="Old journal"), source_format=FormatId.PLUGIN_SSE
    )
    record = entry.to_dsd_dict()
    assert record["original"] == "Old journal"
    assert "index" not in record


def test_old_native_journal_without_provable_index_or_original_is_not_published():
    entry = restore_extracted_dsd_locator(_saved("QUST CNAM", 1, 1), source_format=FormatId.JSON_DSD)
    with pytest.raises(ValueError, match="索引和原文"):
        entry.to_dsd_dict()


def test_old_native_source_warns_that_original_conditions_may_be_missing(tmp_path):
    snapshot = SourceSnapshot(
        SourceDescriptor("project-source:test", "source.json"),
        FormatId.JSON_DSD,
        "a" * 64,
        0,
        lease_id="project-translation-content",
    )
    entry = _saved("INFO NAM1", 2, 2)
    source = HydratedWriteDraft(
        snapshot, FormatId.JSON_DSD, (entry.snapshot(),), str(tmp_path / "export.json"), 1, RequestContext("test")
    )
    checked = preflight_json_export(JsonExportDraft((source,), FormatId.JSON_DSD), HydratedWritePreflightService())
    assert checked.ready
    assert any(check.warning and check.code == "DSD_LEGACY_SOURCE_FIELDS" for check in checked.checks)
