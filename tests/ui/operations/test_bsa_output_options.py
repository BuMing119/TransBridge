from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.contracts.io.test_localized_hydrated_publication import localized as localized_fixture
from tests.ui.operations.test_strict_write_routes import (
    _context,
    _submit,
    feature as feature_fixture,
    qapp as qapp_fixture,
)
from tests.ui.operations.test_write_execution_feedback import _finish_preflight
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.production import build_operation_plan_facade
from transbridge.ui.operations.write_dialog import WritePlanDialog

localized = localized_fixture
feature = feature_fixture
qapp = qapp_fixture


def _localized_context(case, tmp_path, batch):
    slot = SimpleNamespace(
        esp_path=str(case.source),
        format_id=FormatId.PLUGIN_SSE,
        source_snapshot=case.parsed.source_snapshot,
        collection=TranslationEntryCollection(
            tuple(replace(entry, translation="Translated", stage=1) for entry in case.parsed.entries)
        ),
    )
    slots = {str(case.source): slot}
    if batch:
        slots.update(_context(tmp_path).slots)
    return SimpleNamespace(active_slot=slot, slots=slots, collection=slot.collection)


@pytest.mark.parametrize("batch", [False, True])
def test_bsa_option_is_unchecked_by_default_and_can_switch_back_to_loose_output(
    localized, tmp_path, feature, qapp, batch
):
    context = _localized_context(localized, tmp_path, batch)
    values = {"output_directory": str(localized.output)} if batch else {"target_path": str(localized.target)}
    draft = feature.feature.create_draft(context, batch, values)
    dialog = WritePlanDialog(feature.feature.mapper.present("bsa-options", 1, draft), context)
    try:
        checkbox = dialog._field_edits["pack_bsa"]
        assert not checkbox.isChecked()
        assert "默认不勾选" in checkbox.text()
        dialog._overwrite.setChecked(True)
        checkbox.setChecked(True)
        assert not dialog._overwrite.isChecked()
        packed = feature.feature.edit_draft(draft, dialog.edited_values())
        requests = packed.request.drafts if batch else (packed.request,)
        assert all(dict(item.options)["pack_bsa"] for item in requests)
        assert packed.request_digest != draft.request_digest
        checkbox.setChecked(False)
        loose = feature.feature.edit_draft(packed, dialog.edited_values())
        requests = loose.request.drafts if batch else (loose.request,)
        assert all(not dict(item.options)["pack_bsa"] for item in requests)
        result = _submit(feature, loose)
        assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
        assert (localized.output / "Strings").is_dir()
        assert not tuple(localized.output.glob("*.bsa"))
    finally:
        dialog.close()


@pytest.mark.parametrize("batch", [False, True])
def test_bsa_checkbox_invalidates_old_confirmation_and_executes_the_selected_output_mode(
    localized, tmp_path, feature, qapp, batch
):
    context = _localized_context(localized, tmp_path, batch)
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    values = {"output_directory": str(localized.output)} if batch else {"target_path": str(localized.target)}
    dialog = facade.begin_write(context, batch=batch, **values)
    try:
        assert not dialog._field_edits["pack_bsa"].isChecked()
        dialog._preflight_button.click()
        _finish_preflight(facade, dialog)
        assert dialog._confirm_button.isEnabled(), dialog._checks.text()
        token = dialog._preflight.confirmation_token
        dialog._edit_button.click()
        dialog._field_edits["pack_bsa"].setChecked(True)
        assert dialog._preflight is None
        dialog.confirm_requested.emit(dialog._plan.session_id, token)
        assert not feature.tasks.list(feature.owner)
        dialog._preflight_button.click()
        _finish_preflight(facade, dialog)
        assert dialog._confirm_button.isEnabled(), dialog._checks.text()
        assert "打包为 BSA" in dialog._summary.text()
        dialog._confirm_button.click()
        qapp.processEvents()
        assert dialog._checks.property("tbStatusId") == "completed", dialog._checks.text()
        assert not (localized.output / "Strings").exists()
        assert len(tuple(localized.output.glob("*.bsa"))) == 1
        if batch:
            assert (localized.output / "source-0.esp").is_file()
            assert not (localized.output / "source-0.bsa").exists()
    finally:
        dialog.set_execution_running(False)
        dialog.close()


def test_batch_packed_archives_with_shared_base_game_names_are_reported_as_output_conflicts(
    localized, tmp_path, feature
):
    context = _localized_context(localized, tmp_path, False)
    first = context.active_slot
    second = SimpleNamespace(**vars(first))
    first.source_snapshot = replace(
        first.source_snapshot, source=replace(first.source_snapshot.source, uri=str(tmp_path / "Skyrim.esm"))
    )
    second.source_snapshot = replace(
        second.source_snapshot, source=replace(second.source_snapshot.source, uri=str(tmp_path / "Dragonborn.esm"))
    )
    first.esp_path = first.source_snapshot.source.uri
    second.esp_path = second.source_snapshot.source.uri
    context.slots = {first.esp_path: first, second.esp_path: second}
    draft = feature.feature.create_draft(context, True, {"output_directory": str(localized.output), "pack_bsa": True})
    checked = feature.feature.mapper.preflight(draft)
    assert not checked.ready
    assert any(
        item.check_id == "BATCH_OUTPUT_DISJOINT" and "interface.bsa" in item.reason.casefold()
        for item in checked.checks
    )
    assert not tuple(localized.output.iterdir())
