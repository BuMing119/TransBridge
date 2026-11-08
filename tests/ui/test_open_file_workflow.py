"""Real worker, project publication and snapshot restoration for the new entry."""

import threading
import time
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget
import pytest

from tests.contracts.io.test_open_translation_file import FORMATS, source_file
from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.application.io.open_file_options import OpenFileOptions
from transbridge.application.projects import ProjectSourceRequest
from transbridge.ui.coordinators import open_file_coordinator as opening
from transbridge.ui.coordinators.parse_coordinator import ParseCoordinator

project = source_fixtures.project
_APP = QApplication.instance() or QApplication([])


@pytest.fixture
def host(project, monkeypatch):
    monkeypatch.setattr("transbridge.ui.coordinators.parse_coordinator._apply_dictionary_to_collection", lambda _: 0)
    widget = QWidget()
    widget.context = project.ctx
    widget.workers = []
    widget.messages = []
    widget.show_message = lambda message, **_: widget.messages.append(message)
    widget.workbench = SimpleNamespace(
        show_step2_progress=lambda *_: None,
        hide_step2_progress=lambda: None,
        set_step2_parsing=lambda _: None,
    )
    widget.parser = ParseCoordinator(widget)
    widget.opener = opening.OpenFileCoordinator(widget, widget.parser)
    yield widget
    for worker in widget.workers:
        worker.wait(5000)
    project.app.processEvents()
    widget.close()


def wait(project, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        project.app.processEvents()
        time.sleep(0.005)
    project.app.processEvents()
    assert predicate()


def start(project, host, paths, **options):
    host.opener.start(
        OpenFileOptions(source_paths=tuple(map(str, paths)), **options),
        context=project.ctx,
        authority=host.parser._capture_authoritative_target(),
    )


@pytest.mark.parametrize("kind", FORMATS)
def test_open_save_and_restore_all_formats_without_original(project, host, kind):
    path = source_file(project.root, kind)
    content = path.read_bytes()
    start(project, host, [path])
    wait(project, lambda: not host.opener._running)
    assert len(project.ctx.slots) == 1, host.messages
    slot = project.ctx.active_slot
    assert slot.format_id.value == kind
    assert slot.source_snapshot.content == content
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.gui_project_commands.save(project.request).is_success
    path.unlink()
    restored = project.services.current_project_opener.prepare_active(project.request)
    assert restored.is_success and restored.value.recovery is None, restored.diagnostics
    assert restored.value.hydrations[0].source_snapshot.content == content
    assert [e.translation for e in restored.value.hydrations[0].entries] == [e.translation for e in slot.collection]


def test_batch_retains_successes_and_reports_corrupt_and_duplicate_files(project, host):
    first = source_file(project.root, "binary.eet")
    second = source_file(project.root, "sst.ssu9")
    bad = project.root / "broken.json"
    bad.write_bytes(b"broken")
    start(project, host, [first, bad, second, first])
    wait(project, lambda: not host.opener._running)
    assert len(project.ctx.slots) == 2, host.messages
    assert len(project.ctx.project_sources) == 2
    assert not project.ctx.authoritative_projection_diverged()
    assert "部分文件已打开" in host.messages[-1]
    assert "broken.json" in host.messages[-1]


def test_optional_translation_is_committed_with_initial_source(project, host):
    donor = source_file(project.root, "xml.eet")
    path = project.root / "Test.esp"
    start(project, host, [path], translation_path=str(donor))
    wait(project, lambda: not host.opener._running)
    assert next(iter(project.ctx.collection)).translation == "你好", host.messages
    assert not project.ctx.authoritative_projection_diverged()
    assert project.services.project_lifecycle.active.variant.snapshot().entries[0].translation == "你好"


def test_changed_context_during_worker_prevents_publication(project, host, monkeypatch):
    path = source_file(project.root, "binary.eet")
    ready, release = threading.Event(), threading.Event()
    prepare = opening.prepare_open_file

    def delayed(*args, **kwargs):
        result = prepare(*args, **kwargs)
        ready.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(opening, "prepare_open_file", delayed)
    start(project, host, [path])
    try:
        wait(project, ready.is_set)
        assert not project.ctx.project_sources
        host.context = SimpleNamespace()
    finally:
        release.set()
    wait(project, lambda: not host.opener._running)
    assert not project.ctx.project_sources
    assert not project.ctx.slots


def test_cancel_dialog_starts_no_worker(project, host):
    dialog = host.opener.open()
    dialog.reject()
    project.app.processEvents()
    assert not host.workers
    assert not host.opener._dialogs


def test_revision_changed_during_preparation_rejects_commit(project, host, monkeypatch):
    path = source_file(project.root, "binary.eet")
    ready, release = threading.Event(), threading.Event()
    prepare = opening.prepare_open_file

    def delayed(*args, **kwargs):
        result = prepare(*args, **kwargs)
        ready.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(opening, "prepare_open_file", delayed)
    start(project, host, [path])
    try:
        wait(project, ready.is_set)
        added = project.ctx.project_commands.add_source(ProjectSourceRequest(str(project.source)), project.request)
        assert added.is_success, added.diagnostics
    finally:
        release.set()
    wait(project, lambda: not host.opener._running)
    assert len(project.ctx.project_sources) == 1
    assert not project.ctx.slots
    assert "已变化" in host.messages[-1]


def test_commit_uses_prepared_snapshot_after_original_disappears(project, host, monkeypatch):
    path = source_file(project.root, "binary.eet")
    content = path.read_bytes()
    prepare = opening.prepare_open_file

    def remove_after_prepare(*args, **kwargs):
        result = prepare(*args, **kwargs)
        path.unlink()
        return result

    monkeypatch.setattr(opening, "prepare_open_file", remove_after_prepare)
    start(project, host, [path])
    wait(project, lambda: not host.opener._running)
    assert project.ctx.active_slot.source_snapshot.content == content, host.messages
    assert len(project.ctx.project_sources) == 1


def test_dialog_from_previous_context_cannot_submit(project, host):
    dialog = host.opener.open()
    dialog.source_input.setText(str(project.source))
    host.context = SimpleNamespace()
    dialog.accept()
    project.app.processEvents()
    assert not host.workers
    assert "已变化" in host.messages[-1]
