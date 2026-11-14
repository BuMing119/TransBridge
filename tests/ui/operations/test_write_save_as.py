from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QPushButton, QStyle, QStyleOptionComboBox
import pytest

from tests.contracts.io.test_localized_hydrated_publication import localized as localized_fixture
from tests.ui.operations.test_export_json_dialog import _finish_export
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


def test_plugin_output_choice_creates_sst_from_saved_source(tmp_path, feature):
    from transbridge.parser.xt.sst_codec import SstDocument

    context = _context(tmp_path)
    source = Path(next(iter(context.slots)))
    original = source.read_bytes()
    initial = feature.feature.create_draft(context, False, {})
    choices = next(field for field in initial.editable_fields if field.field_id == "output_format")
    assert ("sst.ssu9", "xTranslator SST SSU9（实验性）") in choices.options
    target = tmp_path / "dictionary.sst"
    edited = feature.feature.edit_draft(initial, (("output_format", "sst.ssu9"), ("target_path", str(target))))
    assert edited.request.format_id is FormatId.SST_SSU9
    assert edited.request.source_snapshot.format_id is FormatId.PLUGIN_SSE
    result = _submit(feature, edited)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    document = SstDocument.from_bytes(target.read_bytes())
    assert [record.translation for record in document.records] == ["译文0", "译文1"]
    assert source.read_bytes() == original
    assert len(result.artifact_refs) == 1


def test_sst_output_selection_clears_stale_plugin_path_and_overwrite(qapp, tmp_path, feature):
    context = _context(tmp_path)
    initial = feature.feature.create_draft(context, False, {})
    plan = feature.feature.mapper.present("sst-output", 1, initial)
    dialog = WritePlanDialog(plan, context)
    dialog._field_edits["target_path"].setText(str(tmp_path / "old.esp"))
    dialog._field_edits["overwrite_confirmed"].setChecked(True)
    choice = dialog._field_edits["output_format"]
    choice.setCurrentIndex(choice.findData("sst.ssu9"))
    assert dialog._field_edits["target_path"].text() == ""
    assert not dialog._field_edits["overwrite_confirmed"].isChecked()
    dialog.close()


@pytest.mark.parametrize("initial_selection", [False, True])
def test_batch_plugin_sst_choice_publishes_each_dictionary(tmp_path, feature, initial_selection):
    from transbridge.parser.xt.sst_codec import SstDocument

    context = _context(tmp_path, 2)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    output = tmp_path / "dictionaries"
    values = {"output_format": "sst.ssu9", "output_directory": str(output)}
    draft = feature.feature.create_draft(context, True, values if initial_selection else {})
    if not initial_selection:
        draft = feature.feature.edit_draft(draft, tuple(values.items()))
    result = _submit(feature, draft)
    assert result.outcome is OperationOutcome.COMPLETED, result.diagnostics
    for path, content in originals.items():
        assert Path(path).read_bytes() == content
        document = SstDocument.from_bytes((output / Path(path).with_suffix(".sst").name).read_bytes())
        assert len(document.records) == 2
    assert len(result.artifact_refs) == 2


@pytest.mark.parametrize("batch", [False, True])
def test_original_write_requires_mode_selection_and_explicit_overwrite(tmp_path, feature, batch):
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

    draft = feature.feature.edit_draft(draft, (("destination_mode", "original"),))
    assert not feature.feature.mapper.preflight(draft).ready
    assert all(Path(path).read_bytes() == content for path, content in originals.items())
    draft = feature.feature.edit_draft(draft, (("overwrite_confirmed", "true"),))
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


def test_single_directory_uses_no_filename_input_and_original_mode_clears_confirmation(write_dialog, monkeypatch):
    case = write_dialog
    source = Path(next(iter(case.context.slots)))
    destination = source.parent / "output" / source.name
    seen = []

    def choose(_parent, title, initial, **_kwargs):
        seen.append((title, initial))
        return str(destination.parent)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose)
    case.dialog._overwrite.setChecked(True)
    button = next(item for item in case.dialog.findChildren(QPushButton) if item.accessibleName() == "选择输出目录")
    button.click()
    assert seen == [("选择输出目录", str(source.parent))]
    fields = dict(case.dialog.edited_values())
    assert fields["output_directory"] == str(destination.parent)
    assert "target_path" not in fields
    assert not case.dialog._field_edits["target_path"].isVisible()
    assert not case.dialog._overwrite.isChecked()
    case.dialog._overwrite.setChecked(True)
    mode = case.dialog._field_edits["destination_mode"]
    mode.setCurrentIndex(mode.findData("original"))
    assert dict(case.dialog.edited_values())["destination_mode"] == "original"
    assert not case.dialog._field_edits["output_directory"].isVisible()
    assert not case.dialog._overwrite.isChecked()


@pytest.mark.parametrize("format_id", ["sst.ssu9", "json.dsd", "json.paratranz"])
def test_save_original_cannot_overwrite_source_with_another_format(write_dialog, format_id):
    dialog = write_dialog.dialog
    choice = dialog._field_edits["output_format"]
    mode = dialog._field_edits["destination_mode"]
    mode.setCurrentIndex(mode.findData("original"))
    choice.setCurrentIndex(choice.findData(format_id))
    assert not mode.isVisible()
    assert not dialog._save_original_button.isEnabled()
    dialog._restore_output()
    assert dict(dialog.edited_values())["target_path"] == ""
    choice.setCurrentIndex(choice.findData("plugin.sse"))
    assert mode.isVisible()
    assert mode.currentData() == "directory"
    assert dialog._field_edits["output_directory"].isVisible()


def test_save_original_rejects_path_that_disappeared_after_source_capture(write_dialog, feature):
    case = write_dialog
    Path(next(iter(case.context.slots))).unlink()
    mode = case.dialog._field_edits["destination_mode"]
    mode.setCurrentIndex(mode.findData("original"))
    draft = feature.feature.edit_draft(case.draft, case.dialog.edited_values())
    assert not feature.feature.mapper.preflight(draft).ready


def test_directory_mode_blocks_source_overlap_without_asking_to_overwrite(tmp_path, feature, qapp, monkeypatch):
    context = _context(tmp_path)
    source = Path(next(iter(context.slots)))
    original = source.read_bytes()
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    dialog = facade.begin_write(context)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: pytest.fail("Source overlap must block export"))
    try:
        dialog._field_edits["output_directory"].setText(str(source.parent))
        dialog._confirm_button.click()
        _finish_preflight(facade, dialog)
        qapp.processEvents()
        assert "输出与来源插件或其配套资源重叠" in dialog._checks.text()
        assert dialog._field_edits["output_directory"].isEnabled()
        assert source.read_bytes() == original
    finally:
        dialog.close()
        QTest.qWait(20)


def test_output_format_arrow_opens_choice_popup(write_dialog, qapp):
    choice = write_dialog.dialog._field_edits["output_format"]
    option = QStyleOptionComboBox()
    choice.initStyleOption(option)
    arrow = choice.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, choice
    )
    QTest.mouseClick(choice, Qt.MouseButton.LeftButton, pos=arrow.center())
    qapp.processEvents()
    try:
        assert choice.view().isVisible()
        assert not choice.isEditable()
        assert choice.findData("json.dsd") >= 0
        assert choice.findData("json.paratranz") >= 0
    finally:
        choice.hidePopup()


def test_directory_picker_keeps_saved_filename_when_original_is_missing(write_dialog, monkeypatch):
    case = write_dialog
    source = Path(next(iter(case.context.slots)))
    source.unlink()
    destination = source.parent / "exports"
    seen = []

    def choose(_parent, _title, initial, **_kwargs):
        seen.append(initial)
        return str(destination)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose)
    case.dialog._browse_output()

    assert seen == [""]
    assert dict(case.dialog.edited_values())["output_directory"] == str(destination)


@pytest.mark.parametrize("format_id,suffix", [("sst.ssu9", ".sst"), ("json.dsd", ".json"), ("json.paratranz", ".json")])
def test_directory_selection_uses_saved_name_and_selected_format(
    tmp_path, feature, qapp, monkeypatch, format_id, suffix
):
    context = _context(tmp_path)
    slot = context.active_slot
    slot.source_snapshot = replace(
        slot.source_snapshot,
        source=replace(slot.source_snapshot.source, uri="source:0", display_name="保存的文件.esp"),
    )
    draft = feature.feature.create_draft(context, False, {})
    dialog = WritePlanDialog(feature.feature.mapper.present("saved-name", 1, draft), context)
    destination = tmp_path / "导出目录"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a: str(destination))
    try:
        dialog._browse_output()
        assert dict(dialog.edited_values())["output_directory"] == str(destination)
        choice = dialog._field_edits["output_format"]
        choice.setCurrentIndex(choice.findData(format_id))
        assert dict(dialog.edited_values())["target_path"] == ""
        dialog._browse_output()
        assert dict(dialog.edited_values())["target_path"] == str(destination / f"保存的文件{suffix}")
    finally:
        dialog.close()


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize(
    "format_id,suffix",
    [("plugin.sse", ".esp"), ("sst.ssu9", ".sst"), ("json.dsd", ".json"), ("json.paratranz", ".json")],
)
def test_export_needs_only_directory_selection(tmp_path, feature, qapp, monkeypatch, batch, format_id, suffix):
    context = _context(tmp_path, 2 if batch else 1)
    originals = {path: Path(path).read_bytes() for path in context.slots}
    destination = tmp_path / "导出目录"
    destination.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a: str(destination))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_kw: pytest.fail("Must not ask for a filename"))
    facade = build_operation_plan_facade(SimpleNamespace(tasks=feature.tasks), RequestContext("gui"))
    dialog = facade.begin_write(
        context, batch=batch, output_format="auto" if batch and format_id == "plugin.sse" else format_id
    )
    try:
        field = "output_directory" if batch or format_id == "plugin.sse" else "target_path"
        button = dialog._field_edits[field].parentWidget().layout().itemAt(1).widget()
        assert button.accessibleName() == "选择输出目录" and button.isVisible()
        button.click()
        dialog._confirm_button.click()
        _finish_export(dialog, qapp)
        assert dialog._checks.property("tbStatusId") == "completed", dialog._checks.text()
        assert {path.name for path in destination.iterdir()} == {
            Path(path).with_suffix(suffix).name for path in originals
        }
        assert all(Path(path).read_bytes() == content for path, content in originals.items())
    finally:
        dialog.close()


@pytest.mark.parametrize("write_dialog", [True], indirect=True)
def test_batch_save_as_directory_button_and_restore_are_available_in_write_ui(write_dialog, monkeypatch):
    case = write_dialog
    destination = str(Path(next(iter(case.context.slots))).parent / "output")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: destination)
    case.dialog._overwrite.setChecked(True)
    button = next(item for item in case.dialog.findChildren(QPushButton) if item.accessibleName() == "选择输出目录")
    button.click()
    values = dict(case.dialog.edited_values())
    assert values["output_directory"] == destination
    assert values["overwrite_confirmed"] == "false"
    edited = case.dialog._plan.request_digest
    assert case.dialog._preflight is None and case.dialog._confirm_button.isEnabled()
    case.dialog._restore_output()
    assert dict(case.dialog.edited_values())["output_directory"] == ""
    assert case.dialog._plan.request_digest == edited  # The coordinator applies edits only when preflight is requested.


@pytest.mark.parametrize("write_dialog", [False, True], indirect=True)
def test_cancelled_save_as_picker_leaves_destination_and_confirmation_unchanged(write_dialog, monkeypatch):
    dialog = write_dialog.dialog
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *_a, **_kw: pytest.fail("Export must only ask for a directory")
    )
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
        assert not dialog._preflight.ready  # An output directory must be chosen before publication.
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
