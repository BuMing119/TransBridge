from __future__ import annotations

from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication
import pytest

from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.application.io import FormatId
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.tool_writer import _tool_write_back
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.coordinators import parse_coordinator as parse_module
from transbridge.ui.workers import ApiWorker

project = source_fixtures.project
_APP = QApplication.instance() or QApplication([])


def _until(project, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        project.app.processEvents()
        time.sleep(0.005)
    project.app.processEvents()
    assert predicate()


def _plugin(path: Path, index: int) -> None:
    def field(kind, value):
        return struct.pack("<4sH", kind, len(value)) + value

    def record(kind, identifier, value):
        return struct.pack("<4sIIIIHH", kind, len(value), 0, identifier, 0, 44, 0) + value

    weapon = record(
        b"WEAP",
        0x800 + index,
        field(b"EDID", f"Weapon{index}".encode() + b"\0") + field(b"FULL", f"Source {index}".encode() + b"\0"),
    )
    path.write_bytes(
        record(b"TES4", 0, field(b"HEDR", struct.pack("<fII", 1.7, 1, 0x802)))
        + struct.pack("<4sI4sIHHHH", b"GRUP", len(weapon) + 24, b"WEAP", 0, 0, 0, 0, 0)
        + weapon
    )


@pytest.fixture
def parsing(project, monkeypatch):
    TaskManager.reset()
    # Translation-memory files are external user input and irrelevant to source hydration.
    monkeypatch.setattr(parse_module, "_apply_dictionary_to_collection", lambda _collection: 0)
    messages = []
    state = {"parsing": False}
    host = SimpleNamespace(
        context=project.ctx,
        workers=[],
        show_message=messages.append,
        workbench=SimpleNamespace(
            show_step2_progress=lambda *_args: None,
            update_step2_progress=lambda *_args: None,
            hide_step2_progress=lambda: None,
            set_step2_parsing=lambda value: state.update(parsing=value),
        ),
    )
    coordinator = parse_module.ParseCoordinator(host)
    cfg = SimpleNamespace(
        esp_paths=[],
        eet_path=str(project.source),
        xt_path=None,
        tp_path=None,
        strings_dir=None,
        strings_lang="english",
        skip_empty=True,
    )

    def start(kind):
        if kind == "eet":
            coordinator._run_parse_eet(cfg)
            return [str(project.source)]
        paths = [project.root / f"source-{index}.esp" for index in range(1, 3 if kind == "batch" else 2)]
        for index, path in enumerate(paths, 1):
            _plugin(path, index)
        cfg.esp_paths = [str(path) for path in paths]
        cfg.eet_path = None
        if kind == "batch":
            coordinator._run_batch_parse_esp(cfg)
        else:
            coordinator._run_parse_esp(cfg)
        return cfg.esp_paths

    yield SimpleNamespace(start=start, host=host, coordinator=coordinator, state=state, messages=messages)
    for worker in host.workers:
        _until(project, lambda w=worker: not w.isRunning())
    TaskManager.reset()


@pytest.mark.parametrize("kind", ["eet", "esp", "batch"])
def test_gui_parse_publishes_authoritative_hydration_usable_by_safe_writer(project, parsing, kind):
    paths = parsing.start(kind)
    _until(project, lambda: not parsing.state["parsing"])
    assert len(project.ctx.slots) == len(paths), parsing.messages
    authority = project.services.project_lifecycle.active.variant.snapshot()
    expected = {entry.entry_key: entry for entry in authority.entries}
    for path in paths:
        slot = project.ctx.slots[path]
        assert slot.source_snapshot is not None
        assert slot.format_id is (FormatId.XML_EET if kind == "eet" else FormatId.PLUGIN_SSE)
        assert set(entry.identity for entry in slot.collection) <= expected.keys()
        for entry in slot.collection:
            assert entry.revision == expected[entry.identity].revision
            assert entry.translation == expected[entry.identity].translation
    assert not project.ctx.authoritative_projection_diverged()

    project.ctx.activate_slot(paths[-1])
    target = project.root / ("written.xml" if kind == "eet" else "written.esp")
    result = _tool_write_back(
        {"target": "eet" if kind == "eet" else "esp", "path": str(target)},
        ExecutionContext(app_context=project.ctx),
    )
    assert result.success, result.message
    handle = TaskManager().get_handle(result.data["task_id"])
    _until(project, lambda: not handle._thread.is_alive())
    assert TaskManager().get_status(result.data["task_id"])["status"] == "completed"
    assert target.is_file()
    assert project.services.gui_project_commands.save(project.request).is_success


@pytest.mark.parametrize("kind", ["eet", "esp", "batch"])
@pytest.mark.parametrize("change", ["variant", "revision"])
def test_late_parse_callback_cannot_publish_stale_hydration(project, parsing, monkeypatch, kind, change):
    committed = threading.Event()
    release = threading.Event()
    number = [0]
    delayed_number = 2 if kind == "batch" else 1

    def delayed_worker(function):
        number[0] += 1
        should_delay = number[0] == delayed_number

        def work():
            result = function()
            if should_delay:
                committed.set()
                assert release.wait(5)
            return result

        return ApiWorker(work)

    monkeypatch.setattr(parse_module, "ApiWorker", delayed_worker)
    paths = parsing.start(kind)
    try:
        _until(project, committed.is_set)
        assert not project.ctx.slots
        previous_identity = project.ctx.active_version_identity
        commands = project.services.gui_project_commands
        if change == "variant":
            assert commands.save(project.request).is_success
            switched = commands.create_variant("Next", project.request, copy_active=True)
            assert switched.is_success, switched.diagnostics
            assert project.ctx.active_version_identity != previous_identity
        else:
            entry = project.services.project_lifecycle.active.variant.snapshot().entries[0]
            changed = commands.update_entry(entry.entry_key, project.request, translation="newer edit", stage=1)
            assert changed.is_success, changed.diagnostics
        before = project.services.project_lifecycle.active.variant.snapshot()
        release.set()
        _until(project, lambda: not parsing.state["parsing"])
        assert all(path not in project.ctx.slots for path in paths)
        assert project.services.project_lifecycle.active.variant.snapshot() == before
        assert any("未发布过期集合" in message for message in parsing.messages)
    finally:
        release.set()


def test_batch_parse_continues_after_invalid_source_without_discarding_valid_hydration(project, parsing, monkeypatch):
    valid_plugin = _plugin

    def source_file(path, index):
        if index == 1:
            path.write_bytes(b"invalid plugin")
        else:
            valid_plugin(path, index)

    monkeypatch.setattr("tests.ui.test_parse_hydration_authority._plugin", source_file)
    paths = parsing.start("batch")
    _until(project, lambda: not parsing.state["parsing"])
    assert list(project.ctx.slots) == paths[1:]
    assert project.ctx.slots[paths[1]].source_snapshot is not None
    assert len(project.services.project_lifecycle.active.variant.snapshot().entries) == 1
    assert any("成功 1 个，失败 1 个" in message for message in parsing.messages)
