"""Batch output formats preserve native SST and recompute converted filenames."""

from pathlib import Path

import pytest

from tests.ui.operations.test_strict_write_routes import _context, _submit, feature as feature_fixture
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io.contracts import FormatId, SourceDescriptor, SourceSnapshot
from transbridge.application.io.operation_write import HydratedWriteDraft
from transbridge.application.io.publish import ConflictPolicy
from transbridge.converter.translation_entry import TranslationEntry
from transbridge.parser.xt.sst_codec import SstDocument
from transbridge.ui.operations.write_batch import (
    _HydratedWriteBatch,
    _write_batch_plan,
    _WriteBatchFailure,
    retarget_write_batch,
)

feature = feature_fixture


def test_gui_batch_selection_publishes_two_sst_files_and_preserves_plugins(tmp_path, feature):
    context = _context(tmp_path, 2)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    initial = feature.feature.create_draft(context, True, {})
    output = tmp_path / "sst-exports"
    edited = feature.feature.edit_draft(initial, (("output_format", "sst.ssu9"), ("output_directory", str(output))))
    assert edited.request.output_format == "sst.ssu9"
    assert all(item.format_id is FormatId.SST_SSU9 for item in edited.request.drafts)
    result = _submit(feature, edited)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert {path.name for path in output.iterdir()} == {"source-0.sst", "source-1.sst"}
    for path in output.iterdir():
        document = SstDocument.from_bytes(path.read_bytes())
        assert [record.translation for record in document.records] == ["译文0", "译文1"]
    assert all(Path(path).read_bytes() == content for path, content in originals.items())


def _draft(name, format_id, *, options=()):
    snapshot = SourceSnapshot.from_bytes(SourceDescriptor(f"source:{name}", display_name=name), format_id, b"fixture")
    return HydratedWriteDraft(
        snapshot,
        format_id,
        (TranslationEntry("entry", "original", "source", "译文", 1, "NPC_:FULL"),),
        "",
        1,
        RequestContext("batch-format"),
        options=options,
    )


def test_batch_choice_is_available_for_plugin_and_native_sst():
    request = _HydratedWriteBatch((_draft("mod.esp", FormatId.PLUGIN_SSE), _draft("old.sst", FormatId.SST_SSU8)))
    field = next(item for item in _write_batch_plan(request).editable_fields if item.field_id == "output_format")
    assert field.value == "auto"
    assert [value for value, _label in field.options] == ["auto", "sst.ssu9"]


def test_batch_sst_switch_changes_plugin_suffix_and_keeps_native_ssu8(tmp_path):
    request = _HydratedWriteBatch((
        _draft("mod.esm", FormatId.PLUGIN_SSE, options=(("pack_bsa", True),)),
        _draft("old.sst", FormatId.SST_SSU8),
        _draft("new.sst", FormatId.SST_SSU9),
    ))
    original = retarget_write_batch(request, str(tmp_path), overwrite=False)
    converted = retarget_write_batch(original, str(tmp_path), overwrite=False, output_format="sst.ssu9")
    assert [item.format_id for item in converted.drafts] == [FormatId.SST_SSU9, FormatId.SST_SSU8, FormatId.SST_SSU9]
    assert [Path(item.target_path).name for item in converted.drafts] == ["mod.sst", "old.sst", "new.sst"]
    assert all(not dict(item.options)["pack_bsa"] for item in converted.drafts)
    assert all(item.conflict_policy is ConflictPolicy.FAIL for item in converted.drafts)
    assert converted.output_format == "sst.ssu9"
    assert "SSU9" in _write_batch_plan(converted).mode_summary
    assert not list(tmp_path.iterdir())


def test_returning_to_auto_restores_source_format_and_filename_in_same_directory(tmp_path):
    request = _HydratedWriteBatch((_draft("mod.esl", FormatId.PLUGIN_SSE), _draft("vocab.sst", FormatId.SST_SSU8)))
    converted = retarget_write_batch(request, str(tmp_path), overwrite=False, output_format="sst.ssu9")
    restored = retarget_write_batch(converted, str(tmp_path), overwrite=False, output_format="auto")
    assert [item.format_id for item in restored.drafts] == [FormatId.PLUGIN_SSE, FormatId.SST_SSU8]
    assert [Path(item.target_path).name for item in restored.drafts] == ["mod.esl", "vocab.sst"]
    assert restored.output_format == "auto"


def test_directory_change_preserves_selected_format_and_clearing_removes_targets(tmp_path):
    request = _HydratedWriteBatch((_draft("mod.esp", FormatId.PLUGIN_SSE),))
    converted = retarget_write_batch(request, str(tmp_path / "first"), overwrite=False, output_format="sst.ssu9")
    changed = retarget_write_batch(converted, str(tmp_path / "second"), overwrite=False)
    assert changed.output_format == "sst.ssu9"
    assert changed.drafts[0].target_path == str(tmp_path / "second" / "mod.sst")
    cleared = retarget_write_batch(changed, "", overwrite=False)
    assert cleared.drafts[0].target_path == ""
    assert not _write_batch_plan(cleared).output_ready


@pytest.mark.parametrize("other_format", [FormatId.XML_XT, FormatId.XML_EET])
def test_mixed_xml_batch_has_no_sst_choice_and_rejects_forced_conversion(tmp_path, other_format):
    request = _HydratedWriteBatch((_draft("mod.esp", FormatId.PLUGIN_SSE), _draft("text.xml", other_format)))
    assert not any(item.field_id == "output_format" for item in _write_batch_plan(request).editable_fields)
    with pytest.raises(ValueError, match="SST"):
        retarget_write_batch(request, str(tmp_path), overwrite=False, output_format="sst.ssu9")
    original = retarget_write_batch(request, str(tmp_path), overwrite=False)
    assert [item.format_id for item in original.drafts] == [FormatId.PLUGIN_SSE, other_format]


def test_incomplete_source_batch_does_not_offer_unproven_conversion(tmp_path):
    request = _HydratedWriteBatch(
        (_draft("mod.esp", FormatId.PLUGIN_SSE),),
        (_WriteBatchFailure("", "unknown", "SOURCE_MISSING", "missing", "unknown.xml"),),
    )
    assert not any(item.field_id == "output_format" for item in _write_batch_plan(request).editable_fields)
    with pytest.raises(ValueError, match="SST"):
        retarget_write_batch(request, str(tmp_path), overwrite=False, output_format="sst.ssu9")
    unchanged = retarget_write_batch(request, str(tmp_path), overwrite=False)
    assert unchanged.failures[0].target_path == str(tmp_path / "unknown.xml")
