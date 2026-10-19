"""Real confirmation dialogs keep destructive commands behind an explicit Yes."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget
import pytest

from transbridge.application.terminology.conflicts import ConflictResolutionOperation
from transbridge.application.terminology.models import DecisionStatus
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.coordinators.project_transfer_coordinator import ProjectTransferCoordinator
from transbridge.ui.coordinators.variant_coordinator import VariantCoordinator
from transbridge.ui.tools.dictionary_panel import DictionaryPanel
from transbridge.ui.tools.terminology.conflict_review_dialog import ConflictReviewDialog
from transbridge.ui.tools.terminology.draft_interactions import DraftInteractionController
from transbridge.ui.workbench import step1
from transbridge.ui.workbench.widget import WorkbenchWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Signal:
    def connect(self, callback):
        self.callback = callback

    def emit(self, value):
        self.callback(value)


class _LoadedWorker:
    """Deliver a parsed collection without touching a user file or a thread."""

    def __init__(self, _function):
        self.result = _Signal()
        self.error = _Signal()

    def start(self):
        self.result.emit(["new entry"])


@pytest.fixture
def command_host(qapp, monkeypatch):
    host = QWidget()
    original = SimpleNamespace(label="Original collection")
    context = SimpleNamespace(active_key="fixture.json", slots={"fixture.json": original})
    context.uses_authoritative_projection = False

    def remove_slot(key):
        del context.slots[key]
        context.active_key = None

    context.remove_slot = remove_slot
    context.add_slot = lambda key, slot: context.slots.update({key: slot})
    context.activate_slot = lambda key: setattr(context, "active_key", key)
    host.context = host._ctx = context
    host.show_message = Mock()
    host._status_lbl = QLabel(host)
    host._progress = QWidget(host)
    host._parse_btn = QPushButton(host)
    host._set_locked = Mock()
    host._update_migration_buttons = Mock()
    host.parse_finished = SimpleNamespace(emit=Mock())
    host._parse_presenter = SimpleNamespace(track=Mock())
    for name in ("_esp_input", "_eet_input", "_xt_input", "_tp_input", "_strings_input"):
        setattr(host, name, QLineEdit(host))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: ("fixture.json", ""))
    yield host, original
    host.close()
    host.deleteLater()
    qapp.processEvents()


def _variant_command(host, authoritative):
    variants = [{"id": "selected", "name": "Selected"}, {"id": "other", "name": "Other"}]
    host.context.uses_authoritative_projection = authoritative
    host.context.project_variants = variants
    host.context.active_variant_id = "other"
    host.context.dirty = False

    def remove(name, *_args):
        variants[:] = [item for item in variants if item["id"] != name]

    if authoritative:
        host.runtime_context = object()
        host.project_commands = SimpleNamespace(delete_variant=remove)
        host.start_foreground_task = lambda command, **_kwargs: command()
    else:
        host.context.active_variant = "other"
        host.context.active_project = SimpleNamespace(
            variants=variants,
            variant_dir=lambda _name: SimpleNamespace(exists=lambda: False),
            remove_variant=remove,
            save=Mock(),
        )
        host.workbench = SimpleNamespace(project_bar=SimpleNamespace(refresh=Mock()))
    coordinator = VariantCoordinator(host)
    return lambda: coordinator.delete_variant("selected"), lambda: len(variants) == 1


def _transfer_command(host):
    imported = []
    tasks = []

    def start(command, **kwargs):
        tasks.append(command)
        if len(tasks) == 1:
            kwargs["on_result"](("Fixture", SimpleNamespace(exists=lambda: True)))
        else:
            # The destructive extraction task is queued only after confirmation.
            imported.append(command)

    host.start_foreground_task = start
    coordinator = ProjectTransferCoordinator(host)
    return lambda: coordinator.import_transbridge("fixture.transbridge"), lambda: bool(imported)


def _conflict_command(host):
    details = SimpleNamespace(
        conflict=SimpleNamespace(normalized_original="Dragon", variants=()),
        decisions=(),
        plugin_ids=(),
        evidence=(),
    )
    dialog = ConflictReviewDialog(details, host)
    dialog.operation.setCurrentIndex(dialog.operation.findData(ConflictResolutionOperation.IGNORE))
    return dialog._save, lambda: dialog.choice is not None


def _draft_command(host, operation="suppress"):
    selected = SimpleNamespace(
        term_id="dragon", original="Dragon", suppressed=False, status=DecisionStatus.MANUAL_CONFIRMED
    )
    draft = SimpleNamespace(decisions=(selected,), ref="draft-1")
    applied = []

    def batch_terms(identities, requested, *, expected_ref):
        assert identities == ("dragon",) and requested == operation and expected_ref == "draft-1"
        applied.append(requested)

    presenter = SimpleNamespace(term_editing=SimpleNamespace(batch_terms=batch_terms))
    controller = DraftInteractionController(host, presenter, lambda command: command())
    return lambda: controller._batch_ready(draft, (selected,), operation), lambda: bool(applied)


def _command(case, host, original, monkeypatch):
    if case.startswith("variant-"):
        return _variant_command(host, case == "variant-v2")
    if case == "project-overwrite":
        return _transfer_command(host)
    if case == "term-exclude":
        return _conflict_command(host)
    if case == "term-suppress":
        return _draft_command(host)
    if case == "dictionary-overwrite":
        state = {"dictionary": "original"}

        def import_dict(_path, *, overwrite=False):
            if overwrite:
                state["dictionary"] = "imported"
            return overwrite

        host._manager = SimpleNamespace(import_dict=import_dict)
        host._rebuild_combo = host._refresh_table = Mock()
        return lambda: DictionaryPanel._on_import_dict(host), lambda: state["dictionary"] == "imported"
    if case in ("step-remove", "workbench-remove"):
        method = step1.Step1SourceWidget._on_remove_slot if case == "step-remove" else WorkbenchWidget._on_remove_slot
        return lambda: method(host), lambda: not host.context.slots
    slot = SimpleNamespace(label="Imported collection")

    def changed():
        return host.context.slots["fixture.json"] is not original

    if case == "parse-overwrite":
        coordinator = ParseCoordinator(host)
        coordinator._can_publish_authoritative_source = lambda _authority: True
        coordinator._save_source_to_project = Mock()
        return lambda: coordinator._finish_parse("fixture.json", slot, ["new entry"]), changed
    if case == "step-parse-overwrite":
        return lambda: step1.Step1SourceWidget._finish_parse(host, "fixture.json", slot, ["new entry"]), changed
    assert case == "step-json-overwrite"
    monkeypatch.setattr(step1, "ApiWorker", _LoadedWorker)
    return lambda: step1.Step1SourceWidget._on_import_json(host), changed


_CASES = (
    "variant-v2",
    "variant-legacy",
    "parse-overwrite",
    "project-overwrite",
    "step-remove",
    "step-json-overwrite",
    "step-parse-overwrite",
    "workbench-remove",
    "dictionary-overwrite",
    "term-exclude",
    "term-suppress",
)


def _answer_next(qapp, interaction, observations, errors):
    def answer():
        box = qapp.activeModalWidget()
        try:
            assert isinstance(box, QMessageBox)
            qapp.processEvents()
            no = box.button(QMessageBox.StandardButton.No)
            yes = box.button(QMessageBox.StandardButton.Yes)
            observations.append((box.defaultButton() is no, no.hasFocus()))
            if interaction == "yes":
                QTest.mouseClick(yes, Qt.MouseButton.LeftButton)
            elif interaction == "escape":
                yes.setFocus()
                QTest.keyClick(yes, Qt.Key.Key_Escape)
            else:
                QTest.keyClick(box, Qt.Key.Key_Return)
        except BaseException as exc:
            errors.append(exc)
            if isinstance(box, QMessageBox):
                box.reject()

    QTimer.singleShot(0, answer)


@pytest.mark.parametrize("case", _CASES)
@pytest.mark.parametrize("interaction", ("enter", "escape", "yes"))
def test_destructive_commands_require_explicit_yes(qapp, command_host, monkeypatch, case, interaction):
    host, original = command_host
    invoke, changed = _command(case, host, original, monkeypatch)
    observations, errors = [], []
    _answer_next(qapp, interaction, observations, errors)
    invoke()
    if errors:
        raise errors[0]
    assert changed() is (interaction == "yes"), f"{case}: {interaction} changed the selected data"
    assert observations == [(True, True)], "No must be both the default and initial keyboard focus"


@pytest.mark.parametrize("operation", ("confirm", "reenable"))
def test_non_destructive_term_actions_keep_their_existing_default(qapp, command_host, operation):
    host, _original = command_host
    invoke, changed = _draft_command(host, operation)
    observations, errors = [], []
    _answer_next(qapp, "enter", observations, errors)
    invoke()
    if errors:
        raise errors[0]
    assert changed()
    assert observations == [(False, False)]
