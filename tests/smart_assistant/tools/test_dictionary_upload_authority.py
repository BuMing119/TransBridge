"""Compare workbench dictionary writes with the assistant on a real projected project."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from xml.etree import ElementTree

from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox, QPushButton
import pytest

from tests.smart_assistant.tools.test_source_import_authority import load_source, project as project_fixture
from transbridge.application.io.publish import CommitDecision
from transbridge.application.tasks.controls import CancellationToken
from transbridge.converter.translation_entry_collection import TranslationEntryCollection
from transbridge.smart_assistant.request_scope import apply_captured_scope
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools.tool_migrator import _tool_apply_dictionary, _tool_save_dictionary
from transbridge.smart_assistant.tools.tool_translator import _tool_get_task_result
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.translation_memory.manager import TranslationMemoryManager
from transbridge.ui.context import CollectionSlot
from transbridge.ui.project_rows import ProjectRowsCollection, build_paged_slots
from transbridge.ui.tools.conflict_dialog import DictionaryConflictDialog
from transbridge.ui.tools.dictionary_dialog import SaveToDictionaryDialog
from transbridge.ui.tools.dictionary_panel import DictionaryPanel

project = project_fixture


@pytest.fixture(scope="session", autouse=True)
def dictionary_qapp():
    # ApiWorker's application-owned Qt status buses must outlive successive projects.
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dictionary(project, monkeypatch):
    directory = project.root / "dictionary"
    monkeypatch.setattr(TranslationMemoryManager, "default_dir", lambda self: directory)
    return TranslationMemoryManager()


@pytest.mark.parametrize("endpoint", ["workbench", "assistant"])
def test_dictionary_uses_current_source_before_other_dictionaries_and_preserves_stage(
    project, dictionary, monkeypatch, endpoint
):
    entry = load_source(project)
    project.ctx.eet_path = str(project.source)
    dictionary.add(entry.key, entry.original, "Project value", mod_file_id="OtherMod", scope="project")
    dictionary.add(entry.key, entry.original, "Source value", mod_file_id=project.source.stem)
    dictionary.save()
    before = project.services.project_lifecycle.active.variant.snapshot()
    if endpoint == "assistant":
        context = ExecutionContext(app_context=project.ctx)
        request = SimpleNamespace(source_message_ids=(), execution_version_json="")
        apply_captured_scope(request, (), context)
        assert context.active_slot is None
        previous_results = _tool_get_task_result({}, context).data
        result = _tool_apply_dictionary({}, context)
        assert result.success, result.message
        assert result.data["applied"] == result.data["key_hits"] == 1
        assert result.data["conflicts"] == 0
        assert _tool_get_task_result({}, context).data == previous_results
    else:
        monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
        panel = DictionaryPanel(project.ctx)
        try:
            panel._apply_btn.click()
        finally:
            panel.close()
    current = project.services.project_lifecycle.active.variant.snapshot()
    assert current.revision == before.revision + 1
    assert current.entries[0].translation == project.ctx.collection.get(entry.identity).translation == "Source value"
    assert current.entries[0].stage == before.entries[0].stage
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.gui_project_commands.save(project.request).is_success
    persisted = project.services.variants.load(current.ref).value.envelope.data["entries"][0]
    assert (persisted["translation"], persisted["stage"]) == ("Source value", entry.stage)


@pytest.mark.parametrize("choice", ["alternative", "cancel", "unavailable", "concurrent_edit"])
def test_dictionary_review_controls_the_authoritative_write(project, dictionary, monkeypatch, choice):
    entry = load_source(project)
    dictionary.add(entry.key, entry.original, "First", mod_file_id="Preferred", scope="project")
    dictionary.add(entry.key, entry.original, "Second", mod_file_id="Other", scope="global")
    dictionary.save()
    before = project.services.project_lifecycle.active.variant.snapshot()
    seen = []

    def review(dialog):
        assert QThread.currentThread() == project.ctx.thread()
        (combo,) = dialog.findChildren(QComboBox)
        seen.append([combo.itemData(index) for index in range(combo.count())])
        assert combo.currentData() == "First"
        assert project.ctx.collection.get(entry.identity).translation == ""
        if choice == "concurrent_edit":
            assert project.ctx.update_projected_entry(entry.identity, translation="Manual edit", stage=1).is_success
        combo.setCurrentIndex(1)
        return QDialog.DialogCode.Rejected if choice == "cancel" else QDialog.DialogCode.Accepted

    monkeypatch.setattr(DictionaryConflictDialog, "exec", review)
    if choice == "unavailable":
        monkeypatch.setattr(project.ctx, "review_dictionary_conflicts", None)
    context = ExecutionContext(app_context=project.ctx)
    with ThreadPoolExecutor(max_workers=1) as worker:
        call = worker.submit(_tool_apply_dictionary, {}, context)
        deadline = monotonic() + 5
        while not call.done() and monotonic() < deadline:
            QTest.qWait(5)
        result = call.result(timeout=1)
    if choice == "unavailable":
        assert not result.success
        assert project.services.project_lifecycle.active.variant.snapshot() == before
        return
    assert seen == [["First", "Second"]]
    current = project.services.project_lifecycle.active.variant.snapshot()
    assert current.revision == before.revision + 1
    if choice == "concurrent_edit":
        assert not result.success
        expected = "Manual edit"
    else:
        assert result.success and result.data["applied"] == result.data["conflicts"] == 1, result.message
        expected = "First" if choice == "cancel" else "Second"
    assert current.entries[0].translation == project.ctx.collection.get(entry.identity).translation == expected
    assert not project.ctx.authoritative_projection_diverged()


@pytest.mark.parametrize("args", [{"overwrite": True}, {"source_locale": "en"}, {"target_locale": "fr"}])
def test_dictionary_removed_arguments_cannot_start_a_write(project, dictionary, args):
    entry = load_source(project)
    dictionary.add(entry.key, entry.original, "Value", mod_file_id="Terms")
    dictionary.save()
    before = project.services.project_lifecycle.active.variant.snapshot()
    spec = ToolRegistry.get("apply_dictionary")
    assert not spec.parameters.get("properties")
    result = _tool_apply_dictionary(args, ExecutionContext(app_context=project.ctx))
    assert not result.success
    assert project.services.project_lifecycle.active.variant.snapshot() == before


def test_loaded_empty_collection_returns_zero_counts(current_project_factory, tmp_path, monkeypatch):
    project = current_project_factory(TranslationEntryCollection())
    monkeypatch.setattr(TranslationMemoryManager, "default_dir", lambda self: tmp_path)
    before = project.aggregate.snapshot()
    result = _tool_apply_dictionary({}, ExecutionContext(app_context=project.context))
    assert result.success, result.message
    assert all(
        result.data[key] == 0 for key in ("applied", "key_hits", "text_hits", "misses", "needs_review", "conflicts")
    )
    assert project.aggregate.snapshot() == before


def test_dictionary_cannot_follow_a_changed_mod_path_in_the_same_slot(project, dictionary):
    entry = load_source(project)
    project.ctx.eet_path = str(project.source)
    context = ExecutionContext(app_context=project.ctx)
    project.ctx.eet_path = str(project.root / "new.xml")
    dictionary.add(entry.key, entry.original, "Unrelated mod", mod_file_id="new")
    dictionary.save()
    before = project.services.project_lifecycle.active.variant.snapshot()
    result = _tool_apply_dictionary({}, context)
    assert not result.success
    assert project.services.project_lifecycle.active.variant.snapshot() == before


@pytest.mark.parametrize("interruption", ["request_cancelled", "source_switched"])
def test_scope_change_during_dictionary_review_discards_all_choices(project, dictionary, monkeypatch, interruption):
    entry = load_source(project)
    for mod in ("First", "Second"):
        dictionary.add(entry.key, entry.original, mod, mod_file_id=mod)
    dictionary.save()
    context = ExecutionContext(app_context=project.ctx)
    stopped = Event()
    context.cancellation_token = CancellationToken(stopped)
    before = project.services.project_lifecycle.active.variant.snapshot()
    original_slot = project.ctx.active_slot

    def review(choices):
        if interruption == "request_cancelled":
            stopped.set()
        else:
            project.ctx.add_slot("other.esp", CollectionSlot("Another source", TranslationEntryCollection()))
        return [(choices[1]["entry_id"], choices[1]["translation"])]

    monkeypatch.setattr(project.ctx, "review_dictionary_conflicts", review)
    result = _tool_apply_dictionary({}, context)
    assert not result.success
    assert stopped.is_set() if interruption == "request_cancelled" else project.ctx.active_key == "other.esp"
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert original_slot.collection.get(entry.identity).translation == ""


@pytest.mark.parametrize(
    ("match", "counts"),
    [("changed_original", (1, 0, 0, 1)), ("text", (0, 1, 0, 0)), ("miss", (0, 0, 1, 0))],
)
def test_dictionary_reports_desktop_match_outcomes(project, dictionary, match, counts):
    entry = load_source(project)
    dictionary.add(
        entry.key if match == "changed_original" else "different key",
        entry.original if match == "text" else "Outdated original",
        "Value",
        mod_file_id="Terms",
    )
    dictionary.save()
    context = ExecutionContext(app_context=project.ctx)
    result = _tool_apply_dictionary({}, context)
    assert result.success, result.message
    assert tuple(result.data[key] for key in ("key_hits", "text_hits", "misses", "needs_review")) == counts
    expected = int(match != "miss")
    assert result.data["applied"] == expected
    assert project.ctx.collection.get(entry.identity).translation == ("Value" if expected else "")
    before = project.services.project_lifecycle.active.variant.snapshot()
    repeated = _tool_apply_dictionary({}, context)
    assert repeated.success and repeated.data["applied"] == 0
    assert project.services.project_lifecycle.active.variant.snapshot() == before


@pytest.mark.parametrize("endpoint", ["workbench", "assistant"])
def test_save_dictionary_records_desktop_scope_tags_and_default_source_name(project, dictionary, monkeypatch, endpoint):
    entry = load_source(project)
    project.ctx.eet_path = str(project.source)
    assert project.ctx.update_projected_entry(entry.identity, translation="Saved", stage=1).is_success
    if endpoint == "workbench":

        def accept(dialog):
            dialog._scope_combo.setCurrentIndex(1)
            dialog._tags_edit.setText("terms, terms")
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(SaveToDictionaryDialog, "exec", accept)
        monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
        panel = DictionaryPanel(project.ctx)
        try:
            panel._save_btn.click()
            wait_for_qt(lambda: panel._save_btn.isEnabled())
        finally:
            panel.close()
    else:
        ctx = ExecutionContext(app_context=project.ctx)
        assert not _tool_save_dictionary({"source_locale": "en"}, ctx).success
        token = CancellationToken()
        token._cancel("cancelled")
        ctx.cancellation_token = token
        assert not _tool_save_dictionary({}, ctx).success
        assert dictionary.load() == 0
        ctx.cancellation_token = None
        result = _tool_save_dictionary({"scope": "project", "tags": ["terms", "terms"]}, ctx)
        assert result.success and result.data["added"] == 1, result.message
    assert dictionary.load() == 1
    saved = dictionary.dictionaries[project.source.stem]
    assert saved.scope == "project"
    (item,) = saved.entries.values()
    assert item.translation == "Saved" and list(item.tags) == ["terms"]


@pytest.mark.parametrize("endpoint", ["workbench", "assistant"])
def test_selected_dictionary_save_reads_only_exact_project_rows(project, dictionary, monkeypatch, endpoint):
    tree = ElementTree.parse(project.source)
    another = ElementTree.fromstring(ElementTree.tostring(tree.getroot()[0]))
    another.find("ID").text = "00000002"
    another.find("IDSTEXTE").text = "2"
    another.find("ORIGINAL").text = "Another original"
    tree.getroot().append(another)
    tree.write(project.source, encoding="utf-8", xml_declaration=True)
    load_source(project)
    first, second = project.ctx.collection
    for entry in (first, second):
        assert project.ctx.update_projected_entry(entry.identity, translation=entry.original, stage=1).is_success
    assert project.services.gui_project_commands.save(project.request).is_success
    opener = project.services.current_project_opener
    prepared = opener.prepare_active(project.request, paged=True)
    assert prepared.is_success, prepared.diagnostics
    token = project.ctx.begin_project_open()
    try:
        opened = opener.activate(prepared.value, project.request)
        assert opened.is_success, opened.diagnostics
        snapshot = project.services.project_projection.snapshot()
        assert project.ctx.install_project_open(token, snapshot, build_paged_slots(prepared.value.records.paged_read))
    finally:
        project.ctx.finish_project_open(token)
    assert isinstance(project.ctx.collection, ProjectRowsCollection)
    selected = project.ctx.collection.get(second.identity)
    project.ctx.workbench_row_selection = SimpleNamespace(entries=lambda: (selected,))
    reads = []
    freeze = ProjectRowsCollection.freeze_entries

    def observe_freeze(collection):
        read = freeze(collection)

        def observe(keys=None):
            reads.append((keys, QThread.currentThread() == project.ctx.thread()))
            return read(keys)

        return observe

    monkeypatch.setattr(ProjectRowsCollection, "freeze_entries", observe_freeze)
    if endpoint == "workbench":

        def accept(dialog):
            dialog._mod_edit.setText("Terms")
            dialog._radio_selected.setChecked(True)
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(SaveToDictionaryDialog, "exec", accept)
        monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
        panel = DictionaryPanel(project.ctx)
        try:
            panel._save_btn.click()
            wait_for_qt(lambda: panel._save_btn.isEnabled())
        finally:
            panel.close()
    else:
        with ThreadPoolExecutor(max_workers=1) as worker:
            context = ExecutionContext(app_context=project.ctx)
            call = worker.submit(_tool_save_dictionary, {"mod_file_id": "Terms", "selected_only": True}, context)
            wait_for_qt(call.done)
            assert call.result().success, call.result().message
    assert reads == [((selected.identity,), False)]
    assert dictionary.load() == 1
    (saved,) = dictionary.dictionaries["Terms"].entries.values()
    assert saved.translation == selected.translation


def wait_for_qt(predicate):
    deadline = monotonic() + 5
    while not predicate() and monotonic() < deadline:
        QTest.qWait(5)
    assert predicate()


@pytest.mark.parametrize("same_name", [True, False])
@pytest.mark.parametrize("first_endpoint", ["workbench", "assistant"])
def test_open_panel_preserves_later_publications(project, dictionary, monkeypatch, same_name, first_endpoint):
    from transbridge.translation_memory import publication

    entry = load_source(project)
    assert project.ctx.update_projected_entry(entry.identity, translation="Saved", stage=1).is_success
    dictionary.add("initial", "Initial text", "Initial value", mod_file_id="Terms")
    dictionary.save()
    panel = DictionaryPanel(project.ctx)
    earlier_panel = DictionaryPanel(project.ctx) if first_endpoint == "workbench" else None
    target = dictionary.dictionary_path("Terms")
    staged_threads, publication_threads = [], []
    write = publication.atomic_write_json
    replace = Path.replace
    dialogs = iter([("Terms", "first"), ("Terms" if same_name else "Another", "second")])

    def observed_write(path, data):
        staged_threads.append(QThread.currentThread() == project.ctx.thread())
        write(path, data)

    def observed_replace(path, dest):
        if Path(dest).parent == target.parent:
            publication_threads.append(QThread.currentThread() == project.ctx.thread())
        return replace(path, dest)

    def accept(dialog):
        name, tag = next(dialogs)
        dialog._mod_edit.setText(name)
        dialog._tags_edit.setText(tag)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(publication, "atomic_write_json", observed_write)
    monkeypatch.setattr(Path, "replace", observed_replace)
    monkeypatch.setattr(SaveToDictionaryDialog, "exec", accept)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    try:
        if earlier_panel is not None:
            earlier_panel._save_btn.click()
            wait_for_qt(lambda: earlier_panel._save_btn.isEnabled())
        else:
            args = next(dialogs)

            def commit_without_receipt(effect_id, publish):
                assert effect_id == "dictionary-save"
                publish()
                return CommitDecision(False, "ADMISSION_PERSIST_FAILED")

            with ThreadPoolExecutor(max_workers=1) as worker:
                context = ExecutionContext(app_context=project.ctx)
                context.assistant_gate = SimpleNamespace(commit=commit_without_receipt)
                context.assistant_effect_id = "dictionary-save"
                call = worker.submit(_tool_save_dictionary, {"mod_file_id": args[0], "tags": [args[1]]}, context)
                wait_for_qt(call.done)
                assert call.result().success, call.result().message
                assert "助手回执未确认" in call.result().warnings[0]
        earlier_bytes = target.read_bytes()
        panel._save_btn.click()
        wait_for_qt(lambda: panel._save_btn.isEnabled())
        assert staged_threads == publication_threads == [False, False]
        assert dictionary.load() == (1 if same_name else 2)
        terms = dictionary.dictionaries["Terms"]
        assert {item.translation for item in terms.entries.values()} == {"Initial value", "Saved"}
        saved = next(item for item in terms.entries.values() if item.translation == "Saved")
        if same_name:
            assert saved.tags == ["first", "second"]
        else:
            assert target.read_bytes() == earlier_bytes
            assert saved.tags == ["first"]
            assert next(iter(dictionary.dictionaries["Another"].entries.values())).tags == ["second"]
    finally:
        panel.close()
        if earlier_panel is not None:
            earlier_panel.close()


@pytest.mark.parametrize(
    "interruption", ["cancelled", "edited", "invalid_scope", "invalid_name", "missing_gate", "revoked"]
)
def test_dictionary_commit_discards_staged_or_invalid_work(project, dictionary, monkeypatch, interruption):
    from transbridge.translation_memory import publication

    entry = load_source(project)
    assert project.ctx.update_projected_entry(entry.identity, translation="New", stage=1).is_success
    dictionary.add("original", "Original", "Original", mod_file_id="Terms")
    dictionary.save()
    path = dictionary.dictionary_path("Terms")
    original = path.read_bytes()
    staged, release, cancelled = Event(), Event(), Event()
    write = publication.atomic_write_json

    def hold_stage(path, data):
        write(path, data)
        staged.set()
        assert release.wait(5)

    monkeypatch.setattr(publication, "atomic_write_json", hold_stage)
    context = ExecutionContext(app_context=project.ctx)
    context.cancellation_token = CancellationToken(cancelled)
    if interruption == "missing_gate":
        context.assistant_required = True
    elif interruption == "revoked":
        context.assistant_gate = SimpleNamespace(commit=lambda *_: CommitDecision(False, "TURN_LEASE_STALE"))
        context.assistant_effect_id = "revoked-effect"
    will_stage = interruption in {"cancelled", "edited", "revoked"}
    with ThreadPoolExecutor(max_workers=1) as worker:
        call = worker.submit(
            _tool_save_dictionary,
            {
                "mod_file_id": "X:outside" if interruption == "invalid_name" else "Terms",
                "scope": "invalid" if interruption == "invalid_scope" else "project",
            },
            context,
        )
        if will_stage:
            wait_for_qt(staged.is_set)
            if interruption == "cancelled":
                cancelled.set()
            elif interruption == "edited":
                assert project.ctx.update_projected_entry(entry.identity, translation="Later", stage=1).is_success
        release.set()
        wait_for_qt(call.done)
    assert not call.result().success
    assert staged.is_set() == will_stage
    assert path.read_bytes() == original
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize(
    "interruption", ["dialog_cancelled", "invalid_scope", "progress_cancelled", "new_file_cancelled"]
)
def test_workbench_dictionary_rejects_cancelled_and_invalid_work(project, dictionary, monkeypatch, interruption):
    from transbridge.translation_memory import publication

    entry = load_source(project)
    assert project.ctx.update_projected_entry(entry.identity, translation="New", stage=1).is_success
    path = dictionary.dictionary_path("Terms")
    original = None
    if interruption != "new_file_cancelled":
        dictionary.add("original", "Original", "Original", mod_file_id="Terms")
        dictionary.save()
        original = path.read_bytes()
    staged, release = Event(), Event()
    write = publication.atomic_write_json
    errors = []

    def hold_stage(path, data):
        write(path, data)
        staged.set()
        assert release.wait(5)

    def accept(dialog):
        dialog._mod_edit.setText("Terms")
        if interruption == "dialog_cancelled":
            return QDialog.DialogCode.Rejected
        if interruption == "invalid_scope":
            dialog._scope_combo.addItem("Unexpected", "invalid")
            dialog._scope_combo.setCurrentIndex(2)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(publication, "atomic_write_json", hold_stage)
    monkeypatch.setattr(SaveToDictionaryDialog, "exec", accept)
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, message: errors.append(message))
    monkeypatch.setattr(QMessageBox, "information", lambda *_: pytest.fail("Cancelled save reported completion"))
    panel = DictionaryPanel(project.ctx)
    cancel_progress = interruption in {"progress_cancelled", "new_file_cancelled"}
    try:
        panel._save_btn.click()
        if cancel_progress:
            wait_for_qt(staged.is_set)
            panel._save_progress.findChild(QPushButton).click()
    finally:
        release.set()
        wait_for_qt(lambda: panel._save_btn.isEnabled())
        panel.close()
    assert bool(errors) == (interruption == "invalid_scope")
    assert staged.is_set() == cancel_progress
    if original is None:
        assert not path.parent.exists()
    else:
        assert path.read_bytes() == original
        assert list(path.parent.iterdir()) == [path]
