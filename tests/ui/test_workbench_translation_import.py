"""Exercise the actual toolbar, migration dialog and retained background worker."""

import gc
import os
from pathlib import Path
import subprocess
import sys
from textwrap import dedent
import threading
import time
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.plugin_fixtures import write_plugin
from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.application.io import migration_import
from transbridge.smart_assistant.tools.tool_parser import _tool_parse_esp
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator
from transbridge.ui.shell.action_catalog import IntentId
from transbridge.ui.shell.intent_composition import ShellIntentComposition
from transbridge.ui.shell.intent_router import IntentRouter
from transbridge.ui.workbench.widget import WorkbenchWidget

project = source_fixtures.project
_APP = QApplication.instance() or QApplication([])


def test_toolbar_never_starts_an_unowned_json_worker_in_subprocess():
    # The old implementation aborts natively; isolate it from the pytest process.
    script = dedent("""
        import os
        import time
        from types import SimpleNamespace

        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)

        from PyQt6.QtWidgets import QApplication, QFileDialog, QWidget
        from transbridge.converter.translation_entry_collection import TranslationEntryCollection
        from transbridge.ui.shell.action_catalog import IntentId
        from transbridge.ui.workbench.widget import WorkbenchWidget

        app = QApplication([])
        QFileDialog.getOpenFileName = lambda *a, **kw: ("translated.esp", "")
        TranslationEntryCollection.from_json_file = lambda *a: time.sleep(2)
        widget = WorkbenchWidget.__new__(WorkbenchWidget)
        QWidget.__init__(widget)
        widget._ctx = SimpleNamespace()
        widget.show_step2_progress = lambda *a: None
        widget.hide_step2_progress = lambda: None
        bar = widget._build_collection_bar()
        observed = []
        widget.intent_requested.connect(observed.append)
        widget._btn_import.click()
        assert observed == [IntentId.SOURCE_MIGRATE.value]
        app.processEvents()
        bar.close()
        widget.close()
    """)
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(root / "src")},
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _until(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    assert predicate()


def _source(project):
    source = write_plugin(project.root / "source.esp", [(0x800, "TestNpc", "Hello")])
    result = _tool_parse_esp({"path": str(source)}, ExecutionContext(app_context=project.ctx))
    assert result.success, result.message
    return next(iter(project.ctx.collection))


@pytest.fixture
def toolbar(project):
    host = QWidget()
    host.context = project.ctx
    host.workers = []
    host.messages = []
    host.show_message = host.messages.append
    progress = []
    host.workbench = SimpleNamespace(
        show_step2_progress=lambda *_args: progress.append("shown"),
        hide_step2_progress=lambda: progress.append("hidden"),
    )
    host.parse_coordinator = ParseCoordinator(host)
    composition = object.__new__(ShellIntentComposition)
    composition._host = host
    router = IntentRouter()
    router.register(IntentId.SOURCE_MIGRATE, composition._migrate_source, availability=composition._has_collection)
    composition.router = router

    # Build the production toolbar without unrelated remote/AI views.
    widget = WorkbenchWidget.__new__(WorkbenchWidget)
    QWidget.__init__(widget)
    widget._ctx = project.ctx
    bar = widget._build_collection_bar()
    widget.intent_requested.connect(composition.dispatch)
    yield SimpleNamespace(host=host, widget=widget, progress=progress)
    for worker in host.workers:
        worker.wait(5000)
        assert not worker.isRunning()
    project.app.processEvents()
    router.close()
    bar.close()
    widget.close()
    host.close()


def _open_import(toolbar, source):
    toolbar.widget._btn_import.click()
    dialogs = tuple(toolbar.host.parse_coordinator._owned_dialogs)
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.prefill_migration_source(str(source), "plugin", "plugin.sse")
    dialog.accept()


def test_toolbar_import_retains_slow_worker_and_commits_plugin_once(project, toolbar, monkeypatch):
    entry = _source(project)
    source = write_plugin(project.root / "translated.esp", [(0x800, "TestNpc", "你好")])
    before = project.services.project_lifecycle.active.variant.snapshot()
    started = threading.Event()
    release = threading.Event()
    actual_prepare = migration_import.prepare_migration_import

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return actual_prepare(*args, **kwargs)

    monkeypatch.setattr(migration_import, "prepare_migration_import", delayed)
    try:
        _open_import(toolbar, source)
        assert started.wait(2)
        gc.collect()
        project.app.processEvents()
        assert toolbar.host.workers[-1].isRunning()
        assert toolbar.progress == ["shown"]
        assert not project.ctx.collection.get(entry.identity).translation
    finally:
        release.set()
    _until(project.app, lambda: toolbar.progress[-1] == "hidden")

    after = project.services.project_lifecycle.active.variant.snapshot()
    assert after.revision == before.revision + 1
    assert after.entries[0].translation == "你好"
    assert project.ctx.collection.get(entry.identity).translation == "你好"
    assert not project.ctx.authoritative_projection_diverged()
    assert any("新增 1 条译文" in message for message in toolbar.host.messages)

    _open_import(toolbar, source)
    _until(project.app, lambda: toolbar.progress[-1] == "hidden")
    assert project.services.project_lifecycle.active.variant.snapshot() == after
    assert any("新增 0 条译文" in message for message in toolbar.host.messages)


def test_bad_plugin_reports_failure_and_keeps_project_unchanged(project, toolbar):
    _source(project)
    source = project.root / "broken.esp"
    source.write_bytes(b"TES4broken")
    before = project.services.project_lifecycle.active.variant.snapshot()
    collection = project.ctx.collection

    _open_import(toolbar, source)
    _until(project.app, lambda: toolbar.progress[-1] == "hidden")

    assert project.ctx.collection is collection
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert any("迁移失败" in message for message in toolbar.host.messages)


@pytest.mark.parametrize("all_conflicted", [False, True])
def test_toolbar_plugin_conflicts_skip_entries_and_report_count(project, toolbar, all_conflicted):
    entry = _source(project)
    records = [(0x801, "OtherNpc", "第一种"), (0x801, "OtherNpc", "第二种")]
    if not all_conflicted:
        records.insert(0, (0x800, "TestNpc", "你好"))
    source = write_plugin(project.root / "conflicting.esp", records)
    before = project.services.project_lifecycle.active.variant.snapshot()

    _open_import(toolbar, source)
    _until(project.app, lambda: toolbar.progress[-1] == "hidden")

    after = project.services.project_lifecycle.active.variant.snapshot()
    assert after.revision == before.revision + (0 if all_conflicted else 1)
    assert project.ctx.collection.get(entry.identity).translation == ("" if all_conflicted else "你好")
    assert not project.ctx.authoritative_projection_diverged()
    assert any("迁移完成" in message and "2 条无法唯一匹配已跳过" in message for message in toolbar.host.messages)
    assert not any("迁移失败" in message for message in toolbar.host.messages)


def test_project_edit_during_import_rejects_stale_plugin_draft(project, toolbar, monkeypatch):
    entry = _source(project)
    source = write_plugin(project.root / "translated.esp", [(0x800, "TestNpc", "导入译文")])
    ready = threading.Event()
    release = threading.Event()
    actual_prepare = migration_import.prepare_migration_import

    def delayed(*args, **kwargs):
        draft = actual_prepare(*args, **kwargs)
        ready.set()
        assert release.wait(5)
        return draft

    monkeypatch.setattr(migration_import, "prepare_migration_import", delayed)
    try:
        _open_import(toolbar, source)
        assert ready.wait(2)
        result = project.ctx.project_commands.replace_entry_states(
            {entry.identity: ("用户编辑", 1)}, project.ctx.runtime_context
        )
        assert result.is_success
        before = project.services.project_lifecycle.active.variant.snapshot()
    finally:
        release.set()
    _until(project.app, lambda: toolbar.progress[-1] == "hidden")

    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert project.ctx.collection.get(entry.identity).translation != "导入译文"
    assert not any("迁移完成" in message for message in toolbar.host.messages)


def test_toolbar_without_loaded_source_explains_import_requirement(project, toolbar):
    toolbar.widget._btn_import.click()
    assert not toolbar.host.workers
    assert not toolbar.host.parse_coordinator._owned_dialogs
    assert toolbar.host.messages


def test_import_dialog_rejects_target_changed_before_submit(project, toolbar):
    entry = _source(project)
    source = write_plugin(project.root / "translated.esp", [(0x800, "TestNpc", "导入译文")])
    toolbar.widget._btn_import.click()
    dialog = next(iter(toolbar.host.parse_coordinator._owned_dialogs))
    assert dialog.prefill_migration_source(str(source), "plugin")
    result = project.ctx.project_commands.replace_entry_states(
        {entry.identity: ("已编辑", 1)}, project.ctx.runtime_context
    )
    assert result.is_success
    before = project.services.project_lifecycle.active.variant.snapshot()

    dialog.accept()

    assert not toolbar.host.workers
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert any("MIGRATION_TARGET_CHANGED" in message for message in toolbar.host.messages)


def test_import_dialog_does_not_apply_old_source_to_another_variant(project, toolbar):
    _source(project)
    assert project.ctx.project_commands.save(project.request).is_success
    original_identity = project.ctx.active_version_identity
    source = write_plugin(project.root / "translated.esp", [(0x800, "TestNpc", "导入译文")])
    toolbar.widget._btn_import.click()
    dialog = next(iter(toolbar.host.parse_coordinator._owned_dialogs))
    assert dialog.prefill_migration_source(str(source), "plugin")
    assert project.ctx.project_commands.create_variant("Another", project.request, copy_active=True).is_success
    assert project.ctx.active_version_identity != original_identity
    before = project.services.project_lifecycle.active.variant.snapshot()

    dialog.accept()

    assert not toolbar.host.workers
    assert project.services.project_lifecycle.active.variant.snapshot() == before
    assert any("MIGRATION_TARGET_CHANGED" in message for message in toolbar.host.messages)
