from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QFileDialog, QPushButton
import pytest

from tests.contracts.io.test_localized_hydrated_publication import localized as localized_fixture
from tests.ui.operations.test_strict_write_routes import (
    _context,
    _submit,
    _texts,
    feature as feature_fixture,
    qapp as qapp_fixture,
)
from tests.ui.operations.test_write_execution_feedback import _finish_preflight
from transbridge.application.contracts import OperationOutcome, RequestContext
from transbridge.application.io import FormatId
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.ui.operations.production import build_operation_plan_facade
from transbridge.ui.operations.write_dialog import WritePlanDialog

feature = feature_fixture
qapp = qapp_fixture
localized = localized_fixture


@pytest.mark.parametrize("batch", [False, True])
def test_default_write_requires_output_selection_before_explicit_overwrite(tmp_path, feature, batch):
    context = _context(tmp_path, 2 if batch else 1)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    draft = feature.feature.create_draft(context, batch, {})
    requests = draft.request.drafts if batch else (draft.request,)
    assert [item.target_path for item in requests] == [""] * len(requests)
    checked = feature.feature.mapper.preflight(draft)
    assert not checked.ready
    assert any(item.check_id == "OUTPUT" and item.status.value == "blocked" for item in checked.checks)
    assert feature.tasks.list(feature.owner) == ()
    assert all(Path(path).read_bytes() == content for path, content in originals.items())

    target_field = ("output_directory", str(tmp_path)) if batch else ("target_path", next(iter(context.slots)))
    draft = feature.feature.edit_draft(draft, (target_field, ("overwrite_confirmed", "true")))
    result = _submit(feature, draft)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert set(result.artifact_refs) == set(context.slots)
    assert all(_texts(path) == ["译文0", "译文1"] for path in context.slots)
    assert not tuple(tmp_path.glob("*_translated.*"))


def test_missing_source_reference_never_becomes_output_and_saved_content_can_still_export(tmp_path, feature):
    context = _context(tmp_path)
    source = Path(next(iter(context.slots)))
    source.unlink()
    initial = feature.feature.create_draft(context, False, {})

    assert initial.request.target_path == ""
    assert not feature.feature.mapper.preflight(initial).ready
    target = tmp_path / "export.esp"
    selected = feature.feature.edit_draft(initial, (("target_path", str(target)),))
    result = _submit(feature, selected)

    assert result.outcome is OperationOutcome.COMPLETED
    assert _texts(target) == ["译文0", "译文1"]
    assert not source.exists()


def test_batch_save_as_collects_original_names_and_can_change_or_restore_destination(tmp_path, feature):
    context = _context(tmp_path, 2)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    initial = feature.feature.create_draft(context, True, {})
    first = feature.feature.edit_draft(initial, (("output_directory", str(tmp_path / "first")),))
    destination = tmp_path / "另存 目录"
    edited = feature.feature.edit_draft(first, (("output_directory", str(destination)),))
    assert initial.request_digest != first.request_digest != edited.request_digest
    assert all(Path(item.target_path).parent == destination for item in edited.request.drafts)
    assert all(
        Path(item.target_path).name == Path(item.source_snapshot.source.uri).name for item in edited.request.drafts
    )
    restored = feature.feature.edit_draft(edited, (("output_directory", ""),))
    assert [item.target_path for item in restored.request.drafts] == [""] * len(context.slots)
    assert not feature.feature.mapper.preflight(restored).ready

    result = _submit(feature, edited)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert {path.name for path in destination.iterdir()} == {"source-0.esp", "source-1.esp"}
    assert all(_texts(path) == ["译文0", "译文1"] for path in destination.iterdir())
    assert all(Path(path).read_bytes() == content for path, content in originals.items())
    assert not (tmp_path / "first").exists()


def test_batch_output_names_use_saved_names_when_snapshot_uri_is_an_identity(tmp_path, feature):
    context = _context(tmp_path, 2)
    for index, slot in enumerate(context.slots.values()):
        slot.esp_path = None
        slot.source_snapshot = replace(
            slot.source_snapshot,
            source=replace(slot.source_snapshot.source, uri=f"source:{index}", display_name=f"saved-{index}.esp"),
        )
    initial = feature.feature.create_draft(context, True, {})
    directory = tmp_path / "exports"
    selected = feature.feature.edit_draft(initial, (("output_directory", str(directory)),))

    assert [item.target_path for item in initial.request.drafts] == ["", ""]
    assert [item.target_path for item in selected.request.drafts] == [
        str(directory / "saved-0.esp"),
        str(directory / "saved-1.esp"),
    ]


def test_batch_destination_changes_retarget_incomplete_source_receipts_too(tmp_path, feature):
    context = _context(tmp_path, 3, invalid_last=True)
    context.slots[next(reversed(context.slots))].source_snapshot = None
    initial = feature.feature.create_draft(context, True, {})
    destination = tmp_path / "output"
    edited = feature.feature.edit_draft(initial, (("output_directory", str(destination)),))
    assert edited.request.failures[0].target_path == str(destination / "source-2.esp")
    restored = feature.feature.edit_draft(edited, (("output_directory", ""),))
    assert restored.request.failures[0].target_path == ""

    result = _submit(feature, edited)

    assert result.outcome is OperationOutcome.PARTIAL, result.diagnostics
    assert result.value["outcomes"][-1]["object_ref"] == str(destination / "source-2.esp")
    assert not (destination / "source-2.esp").exists()


def test_batch_save_as_does_not_overwrite_same_named_sources_from_different_folders(tmp_path, feature):
    slots = {}
    for name in ("first", "second"):
        folder = tmp_path / name
        folder.mkdir()
        slots.update(_context(folder).slots)
    context = SimpleNamespace(slots=slots)
    destination = tmp_path / "output"
    draft = feature.feature.create_draft(context, True, {"output_directory": str(destination)})

    checked = feature.feature.mapper.preflight(draft)

    assert not checked.ready
    conflict = next(item for item in checked.checks if item.check_id == "BATCH_OUTPUT_DISJOINT")
    assert "source-0.esp" in conflict.reason
    assert not destination.exists()
    assert feature.tasks.list(feature.owner) == ()


def test_batch_save_as_keeps_localized_plugin_and_strings_names_together(tmp_path, feature, localized):
    context = _context(tmp_path)
    context.slots[str(localized.source)] = SimpleNamespace(
        esp_path=str(localized.source),
        source_snapshot=localized.parsed.source_snapshot,
        format_id=FormatId.PLUGIN_SSE,
        collection=TranslationEntryCollection(localized.parsed.entries),
    )
    originals = {path: path.read_bytes() for path in localized.source.parent.rglob("*") if path.is_file()}
    destination = tmp_path / "output"
    draft = feature.feature.create_draft(context, True, {"output_directory": str(destination)})

    result = _submit(feature, draft)

    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    assert {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()} == {
        "source-0.esp",
        "localized.esp",
        "Strings/localized_English.strings",
        "Strings/localized_English.dlstrings",
        "Strings/localized_English.ilstrings",
    }
    assert (destination / "localized.esp").read_bytes() == originals[localized.source]
    assert all(path.read_bytes() == content for path, content in originals.items())


@pytest.fixture
def write_dialog(tmp_path, feature, qapp, request):
    batch = getattr(request, "param", False)
    context = _context(tmp_path, 2 if batch else 1)
    draft = feature.feature.create_draft(context, batch, {})
    dialog = WritePlanDialog(feature.feature.mapper.present("write-save-as", 1, draft), context)
    dialog.show()
    yield SimpleNamespace(dialog=dialog, draft=draft, context=context)
    dialog.set_preflight_running(False)
    dialog.set_execution_running(False)
    dialog.close()


def test_single_save_as_button_preserves_filename_and_restore_requires_new_confirmation(write_dialog, monkeypatch):
    case = write_dialog
    source = Path(next(iter(case.context.slots)))
    destination = source.parent / "output" / source.name
    seen = []

    def choose(_parent, title, initial, _filter, **_kwargs):
        seen.append((title, initial))
        return str(destination), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    case.dialog._overwrite.setChecked(True)
    button = next(item for item in case.dialog.findChildren(QPushButton) if item.accessibleName() == "另存为")
    button.click()
    assert seen == [("另存为", source.name)]
    assert dict(case.dialog.edited_values())["target_path"] == str(destination)
    assert not case.dialog._overwrite.isChecked()
    case.dialog._overwrite.setChecked(True)
    case.dialog._restore_output()
    assert dict(case.dialog.edited_values())["target_path"] == ""
    assert not case.dialog._overwrite.isChecked()


def test_save_picker_does_not_suggest_a_missing_reference(write_dialog, monkeypatch):
    case = write_dialog
    Path(next(iter(case.context.slots))).unlink()
    seen = []

    def choose(_parent, _title, initial, _filter, **_kwargs):
        seen.append(initial)
        return "", ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    case.dialog._browse_output()

    assert seen == [""]
    assert dict(case.dialog.edited_values())["target_path"] == ""


@pytest.mark.parametrize("write_dialog", [True], indirect=True)
def test_batch_save_as_directory_button_and_restore_are_available_in_write_ui(write_dialog, monkeypatch):
    case = write_dialog
    destination = str(Path(next(iter(case.context.slots))).parent / "output")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: destination)
    case.dialog._overwrite.setChecked(True)
    button = next(item for item in case.dialog.findChildren(QPushButton) if item.accessibleName() == "批量另存为")
    button.click()
    values = dict(case.dialog.edited_values())
    assert values["output_directory"] == destination
    assert values["overwrite_confirmed"] == "false"
    edited = case.dialog._plan.request_digest
    assert case.dialog._preflight is None and not case.dialog._confirm_button.isEnabled()
    case.dialog._restore_output()
    assert dict(case.dialog.edited_values())["output_directory"] == ""
    assert case.dialog._plan.request_digest == edited  # The coordinator applies edits only when preflight is requested.


@pytest.mark.parametrize("write_dialog", [False, True], indirect=True)
def test_cancelled_save_as_picker_leaves_destination_and_confirmation_unchanged(write_dialog, monkeypatch):
    dialog = write_dialog.dialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: ("", ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: "")
    dialog._overwrite.setChecked(True)
    before = dialog.edited_values()
    if dialog._plan.is_batch:
        dialog._browse_directory()
    else:
        dialog._browse_output()
    assert dialog.edited_values() == before


def test_batch_save_as_ui_rechecks_destination_and_publishes_all_files(tmp_path, feature, qapp, monkeypatch):
    context = _context(tmp_path, 2)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    destination = tmp_path / "output"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(destination))
    dialog = facade.begin_write(context, batch=True)
    try:
        dialog._preflight_button.click()
        _finish_preflight(facade, dialog)
        assert not dialog._confirm_button.isEnabled()  # An output directory must be chosen.
        dialog._browse_directory()
        assert dialog._preflight is None
        dialog._preflight_button.click()
        _finish_preflight(facade, dialog)
        assert dialog._confirm_button.isEnabled(), dialog._checks.text()
        assert str(destination) in dialog._summary.text()
        token = dialog._preflight.confirmation_token
        dialog._restore_output()
        dialog.confirm_requested.emit(dialog._plan.session_id, token)
        assert feature.tasks.list(feature.owner) == ()
        dialog._browse_directory()
        dialog._preflight_button.click()
        _finish_preflight(facade, dialog)
        dialog._confirm_button.click()
        qapp.processEvents()
        assert not dialog._execution_running
        assert dialog._checks.property("tbStatusId") == "completed", dialog._checks.text()
        assert {path.name for path in destination.iterdir()} == {"source-0.esp", "source-1.esp"}
        assert all(_texts(path) == ["译文0", "译文1"] for path in destination.iterdir())
        assert all(Path(path).read_bytes() == content for path, content in originals.items())
    finally:
        dialog.set_execution_running(False)
        dialog.close()
