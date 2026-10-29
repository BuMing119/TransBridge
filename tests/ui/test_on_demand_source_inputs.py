from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QComboBox, QDialog, QFileDialog, QInputDialog, QLabel
import pytest

from tests.ui.test_project_creation_reuse import _until, shared_plugin_window as shared_plugin_fixture
from tests.ui.test_project_source_hydration import _snapshot, _source
from tests.ui.test_v2_variant_hydration import window_case as variant_window_fixture
from transbridge.application.io import FormatId
from transbridge.application.io.identity import EntryKey, SourceNamespace
from transbridge.application.projects import ProjectSourceRequest
from transbridge.application.projects.source_supplement_models import (
    SourceSupplementChoice,
    SourceSupplementPreview,
    SourceSupplementRow,
)
from transbridge.ui.source_hydration import prepare_project_slots
from transbridge.ui.source_input_batch_dialog import SourceInputBatchDialog
from transbridge.ui.source_input_state import SourceInputScope, missing_source_keys, original_known
from transbridge.ui.source_supplement_dialog import SourceSupplementDialog
from transbridge.ui.tools.ai_translator._window_actions import missing_original_source_keys
from transbridge.ui.tools.ai_translator.scope_presenter import TranslationScope
from transbridge.ui.tools.ai_translator.task_scope import SourceWorkbenchScope, TaskScope
from transbridge.ui.workbench.table_presenter import RenderSession
from transbridge.ui.workbench.translation_model import TranslationTableModel
from transbridge.ui.workbench.translation_table_columns import COL_ORIGINAL, COL_TRANSLATION

shared_plugin_window = shared_plugin_fixture
window_case = variant_window_fixture
_BATCH_OPEN = SourceInputBatchDialog.open


def test_saved_rows_keep_exact_translation_identity_and_known_original_without_snapshot():
    known = EntryKey(SourceNamespace("saved"), "same", "Saved original")
    unknown = EntryKey(SourceNamespace("saved"), "other")
    hidden = EntryKey(SourceNamespace("saved"), "deleted")
    snapshot = _snapshot([
        {"entry_key": known.to_dict(), "translation": "Known translation", "stage": 5, "revision": 7},
        {"entry_key": unknown.to_dict(), "translation": "Unknown translation", "stage": 1},
        {"entry_key": hidden.to_dict(), "translation": "Deleted translation", "stage": 1, "tombstone": True},
    ])
    slots = dict(prepare_project_slots((), snapshot))
    slot = slots["saved"]
    entries = tuple(slot.collection)
    assert slot.source_snapshot is None and {entry.identity for entry in entries} == {known, unknown}
    assert entries[0].original == "Saved original" and original_known(entries[0])
    assert entries[0].revision.value == 7 and entries[0].translation == "Known translation"
    assert not original_known(entries[1])
    model = TranslationTableModel()
    model.install(RenderSession(1, 1, entries), (0, 1), {}, {})
    assert model.index(0, COL_ORIGINAL).data() == "Saved original"
    assert model.index(1, COL_ORIGINAL).data() == "原文待补充"
    assert model.flags(model.index(1, COL_TRANSLATION)) & Qt.ItemFlag.ItemIsEditable
    assert len(snapshot.values["entries"]) == 3
    known_slot = replace(slot, collection=type(slot.collection)((entries[0],)))
    context = SimpleNamespace(slots={"saved": known_slot})
    assert missing_source_keys(context, "originals") == ()
    assert missing_source_keys(context, "template") == ("saved",)


def test_mixed_slots_only_repair_the_selected_missing_source():
    source = _source("ready.esp", "ready")
    missing = EntryKey(SourceNamespace("missing"), "bare")
    snapshot = _snapshot([{"entry_key": missing.to_dict(), "translation": "Retained"}])
    context = SimpleNamespace(slots=dict(prepare_project_slots((source,), snapshot)))
    assert missing_source_keys(context, "template", ("ready",)) == ()
    assert missing_source_keys(context, "originals", ("ready",)) == ()
    assert missing_source_keys(context, "template", ("missing",)) == ("missing",)
    scope = SourceInputScope(context, ("ready",))
    assert tuple(scope.slots) == ("ready",)


def test_ai_original_requirement_uses_selected_entries_not_other_unknown_rows():
    known = EntryKey(SourceNamespace("saved"), "known", "Actual saved original")
    unknown = EntryKey(SourceNamespace("saved"), "unknown")
    slots = dict(
        prepare_project_slots(
            (),
            _snapshot([
                {"entry_key": key.to_dict(), "translation": "Saved translation", "stage": 1} for key in (known, unknown)
            ]),
        )
    )
    slot = slots["saved"]
    context = SimpleNamespace(slots=slots, active_slot=slot, entry_labels={})
    window = SimpleNamespace(
        _ctx=context,
        _view=SimpleNamespace(sources_panel=SimpleNamespace(selected_slots=lambda: (slot,))),
        _task_scope=TaskScope(context, SourceWorkbenchScope(), lambda _entry: ""),
        _scope_presenter=SimpleNamespace(
            state=TranslationScope(preset="selection", selected_entry_ids=frozenset({next(iter(slot.collection)).id}))
        ),
    )
    assert missing_original_source_keys(window) == ()
    window._scope_presenter.state = TranslationScope()
    assert missing_original_source_keys(window) == ("saved",)


def test_correspondence_preview_uses_one_selector_for_large_inputs(shared_plugin_window):
    old = EntryKey(SourceNamespace("legacy:v1"), "old")
    choices = tuple(
        SourceSupplementChoice(EntryKey(SourceNamespace("new"), str(index)), str(index), f"Original {index}")
        for index in range(5000)
    )
    preview = SourceSupplementPreview(
        "token",
        "chosen.esp",
        "chosen.esp",
        3,
        0,
        1,
        5000,
        (SourceSupplementRow(old, "Saved translation", None),),
        choices,
    )
    dialog = SourceSupplementDialog(preview)
    try:
        assert len(dialog.findChildren(QComboBox)) == 1
        assert dialog.choices.choices is choices
        assert any(
            "3 个翻译版本" in label.text() and "待手动对应 1 条" in label.text()
            for label in dialog.findChildren(QLabel)
        )
        dialog.selector.setCurrentIndex(5000)
        assert dialog.mappings() == {old: choices[-1].key}
        dialog.selector.setCurrentIndex(0)
        assert dialog.mappings() == {old: None}
    finally:
        dialog.close()


def _open_without_artifact(case, source_index=None):
    active = case.services.project_lifecycle.active
    path = Path(case.services.projects.path_for(active.project_ref))
    document = json.loads(path.read_bytes())
    if source_index is None:
        source_index = next(
            index
            for index, source in enumerate(document["data"]["sources"])
            if Path(source.get("location") or source.get("legacy", {}).get("path", "")) == case.plugin
        )
    document["data"]["sources"][source_index]["source_artifact_id"] = "f" * 64
    document["data"]["sources"][source_index]["location"] = ""
    path.write_text(json.dumps(document), encoding="utf-8")
    case.window.project_coordinator.open_project_path(str(path))
    _until(lambda: case.window.project_open_worker is None)
    return path


def _drive_batch_list(monkeypatch, *, cancel=False):
    opened = []

    def show(dialog):
        opened.append(dialog)
        _BATCH_OPEN(dialog)
        QTimer.singleShot(
            0, dialog.reject if cancel else lambda: dialog.source_requested.emit(dialog.model.needs[0].key)
        )

    monkeypatch.setattr(SourceInputBatchDialog, "open", show)
    return opened


def test_real_missing_input_edit_save_cancel_map_reopen_and_resume_write(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    window = case.window
    active = case.services.project_lifecycle.active
    variant_path = Path(case.services.variants.path_for(active.formal_variant_ref))
    variant = json.loads(variant_path.read_bytes())
    orphan = {**variant["data"]["entries"][0], "translation": "Unmatched saved translation"}
    orphan["entry_key"] = {**orphan["entry_key"], "local_key": "old-unmatched-key"}
    variant["data"]["entries"].append(orphan)
    variant_path.write_text(json.dumps(variant), encoding="utf-8")
    _open_without_artifact(case)
    slot = window.context.active_slot
    assert slot.source_snapshot is None and window.context.collection
    assert window.workbench.isEnabled() and not window.context.project_view_failed
    entry = next(iter(slot.collection))
    assert window.context.update_projected_entry(entry.identity, translation="Edited without original").is_success
    saved = []
    assert window.save_current_project_async(on_finished=saved.append)
    _until(lambda: bool(saved))
    assert saved == [True] and next(iter(window.context.collection)).translation == "Edited without original"
    monkeypatch.setattr(QInputDialog, "getItem", lambda _p, _t, _l, items, *_args: (items[0], True))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: ("", ""))
    opened = _drive_batch_list(monkeypatch, cancel=True)
    facade = window.operation_plan_facade
    assert facade.begin_write(window.context) is None
    _until(lambda: opened and window.source_input_coordinator._batch.dialog is None)
    assert not window.source_input_coordinator._busy and window.workbench.isEnabled()
    assert next(iter(window.context.collection)).translation == "Edited without original"

    service = window.app_runtime.use_cases.resolve("project_source_supplements")
    previews = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(case.plugin), ""))
    _drive_batch_list(monkeypatch)

    def cancel_mapping(dialog):
        previews.append(dialog)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SourceSupplementDialog, "exec", cancel_mapping)
    facade.begin_write(window.context)
    _until(lambda: bool(previews) and not window.source_input_coordinator._busy)
    assert not service._pending and window.workbench.isEnabled()
    assert next(iter(window.context.collection)).translation == "Edited without original"
    assert window.context.active_slot.source_snapshot is None
    window.source_input_coordinator._batch.dialog.reject()

    def confirm_mapping(dialog):
        previews.append(dialog)
        dialog.selector.setCurrentIndex(0)
        dialog.selector.setCurrentIndex(1)
        assert any("不纳入此次依赖输入" in label.text() for label in dialog.findChildren(QLabel))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SourceSupplementDialog, "exec", confirm_mapping)
    facade.begin_write(window.context)
    _until(lambda: window.context.active_slot.source_snapshot is not None and not window.source_input_coordinator._busy)
    batch = window.source_input_coordinator._batch
    batch.dialog.continued.emit(tuple(need.key for need in batch._needs() if need.ready))
    _until(lambda: facade.active_plan_count == 1 and window.project_open_worker is None)
    assert not service._pending and window.context.active_slot.source_snapshot is not None
    assert next(iter(window.context.collection)).translation == "Edited without original"
    assert len(window.context.slots) == 2
    retained = next(slot for slot in window.context.slots.values() if slot.source_snapshot is None)
    assert next(iter(retained.collection)).translation == "Unmatched saved translation"
    assert not window.context.authoritative_projection_diverged()
    dialog = next(iter(facade._coordinator._owned_windows.values()))
    try:
        assert not dialog._confirm_button.isEnabled()
        dialog._field_edits["target_path"].setText(str(case.root / "chosen-output.esp"))
        dialog._request_preflight()
        _until(lambda: not facade._coordinator._preflight_workers and dialog._preflight is not None)
        assert dialog._preflight.ready, dialog._checks.text()
        assert not (case.root / "chosen-output.esp").exists()
    finally:
        dialog.close()


@pytest.mark.parametrize("window_case", [2], indirect=True)
def test_good_source_action_and_variant_restore_keep_missing_source_editable(window_case, monkeypatch):
    case = window_case
    window = case.window
    _open_without_artifact(case, 1)
    ready = next(key for key, slot in window.context.slots.items() if slot.source_snapshot is not None)
    missing = next(key for key, slot in window.context.slots.items() if slot.source_snapshot is None)
    window.context.activate_slot(ready)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_: pytest.fail("Ready source must not trigger repair"))
    dialog = window.operation_plan_facade.begin_write(window.context)
    assert dialog is not None
    dialog.close()
    assert next(iter(window.context.slots[missing].collection)).translation == "Variant A"
    window.workbench.project_bar._variant_combo.setCurrentIndex(
        window.workbench.project_bar._variant_combo.findData(case.second.identity.value)
    )
    _until(lambda: window.foreground_worker is None and len(window.context.slots) == 2)
    assert any(slot.source_snapshot is None for slot in window.context.slots.values())
    assert all(next(iter(slot.collection)).translation == "Variant B" for slot in window.context.slots.values())


def test_legacy_group_explicitly_selects_existing_source_and_maps_saved_translation(shared_plugin_window, monkeypatch):
    case = shared_plugin_window
    window = case.window
    active = case.services.project_lifecycle.active
    variant_path = Path(case.services.variants.path_for(active.formal_variant_ref))
    variant = json.loads(variant_path.read_bytes())
    variant["data"]["entries"][0]["entry_key"] = {"namespace": "legacy:v1", "local_key": "legacy-unknown-id"}
    variant["data"]["source_fingerprints"] = [{"namespace": "legacy:v1", "sha256": None}]
    variant_path.write_text(json.dumps(variant), encoding="utf-8")
    _open_without_artifact(case)
    assert window.context.active_slot.source_id is None
    prompts = []

    def choose(_parent, title, _label, items, *_args):
        prompts.append((title, items))
        return items[0], True

    monkeypatch.setattr(QInputDialog, "getItem", choose)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(case.plugin), ""))

    def map_legacy(dialog):
        assert dialog.mapping_model.rows[0].matched_key is None
        dialog.selector.setCurrentIndex(1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SourceSupplementDialog, "exec", map_legacy)
    _drive_batch_list(monkeypatch)
    facade = window.operation_plan_facade
    facade.begin_write(window.context)
    _until(lambda: window.context.active_slot.source_snapshot is not None and not window.source_input_coordinator._busy)
    batch = window.source_input_coordinator._batch
    batch.dialog.continued.emit(tuple(need.key for need in batch._needs() if need.ready))
    _until(lambda: facade.active_plan_count == 1)
    dialog = next(iter(facade._coordinator._owned_windows.values()))
    try:
        assert any(title == "选择译文所属来源" and "作为独立来源补充" in items for title, items in prompts)
        assert len(window.context.project_sources) == 1
        assert next(iter(window.context.collection)).translation == "Project A translation"
        assert window.context.active_slot.source_snapshot is not None
        assert not case.services.project_lifecycle.active.missing_inputs
    finally:
        dialog.close()


@pytest.mark.parametrize("stored_ready", ["empty", "partial", "tombstone"])
def test_sparse_ready_source_and_missing_saved_rows_share_one_editable_authority(shared_plugin_window, stored_ready):
    case = shared_plugin_window
    window = case.window
    source = case.root / "Ready.xml"
    raw = Path("tests/contracts/io/fixtures/eet-small.xml").read_text(encoding="utf-8")
    element = raw[raw.index("  <ESP>") : raw.index("  </ESP>") + len("  </ESP>")]
    second = (
        element
        .replace("00000001", "00000002")
        .replace("GreetingTopic", "OtherTopic")
        .replace("Hello, traveler.", "Another actual original")
    )
    source.write_text(raw.replace("</DocumentElement>", f"{second}\n</DocumentElement>"), encoding="utf-8")
    added = case.services.gui_project_commands.add_source(
        ProjectSourceRequest(str(source), FormatId.XML_EET, options=(("source_namespace", "ready:sparse"),)),
        case.request,
    )
    assert added.is_success, added.diagnostics
    assert case.services.gui_project_commands.save(case.request).is_success
    active = case.services.project_lifecycle.active
    variant_path = Path(case.services.variants.path_for(active.formal_variant_ref))
    variant = json.loads(variant_path.read_bytes())
    missing_rows = [row for row in variant["data"]["entries"] if row["entry_key"]["namespace"] != "ready:sparse"]
    ready_rows = [row for row in variant["data"]["entries"] if row["entry_key"]["namespace"] == "ready:sparse"]
    assert len(ready_rows) == 2 and len(missing_rows) == 1
    retained = [] if stored_ready == "empty" else ready_rows[:1]
    if retained:
        retained[0]["translation"] = "Sparse saved translation"
        retained[0]["tombstone"] = stored_ready == "tombstone"
    variant["data"]["entries"] = [*missing_rows, *retained]
    variant_path.write_text(json.dumps(variant), encoding="utf-8")
    _open_without_artifact(case)
    authority = case.services.project_lifecycle.active.variant.snapshot()
    if retained:
        state = next(row for row in authority.entries if row.entry_key == EntryKey.from_dict(retained[0]["entry_key"]))
        assert state.translation == "Sparse saved translation" and state.tombstone == (stored_ready == "tombstone")
    visible = {entry.identity for slot in window.context.slots.values() for entry in slot.collection}
    assert visible == {row.entry_key for row in authority.entries if not row.tombstone}
    assert len(authority.entries) == 3 and len(visible) == (2 if stored_ready == "tombstone" else 3)
    ready = next(slot for slot in window.context.slots.values() if slot.source_snapshot is not None)
    assert {entry.original for entry in ready.collection} <= {"Hello, traveler.", "Another actual original"}
    assert all(original_known(entry) for entry in ready.collection)
    missing = next(slot for slot in window.context.slots.values() if slot.source_snapshot is None)
    assert next(iter(missing.collection)).translation == "Project A translation"
    assert not original_known(next(iter(missing.collection)))
    assert not window.context.authoritative_projection_diverged()
    target = next(iter(ready.collection))
    assert window.context.update_projected_entry(target.identity, translation="Edited actual source row").is_success
    saved = []
    assert window.save_current_project_async(on_finished=saved.append)
    _until(lambda: bool(saved))
    assert saved == [True]
    persisted = json.loads(variant_path.read_bytes())["data"]["entries"]
    assert {EntryKey.from_dict(row["entry_key"]) for row in persisted} == {row.entry_key for row in authority.entries}
    assert sum(bool(row.get("tombstone")) for row in persisted) == (stored_ready == "tombstone")
