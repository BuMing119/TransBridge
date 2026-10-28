"""Assistant navigation installs one exact bound version before acknowledging success."""

from __future__ import annotations

from dataclasses import replace
from itertools import count
import os
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication, QMessageBox
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import FormatId
from transbridge.application.projects import ProjectProvisioningRequest, ProjectSourceRequest
from transbridge.bootstrap.persistence import build_persistence_v2_services
from transbridge.persistence.v2 import ProjectId, ProjectRef, VariantId, VariantRef
from transbridge.ui import context as context_module
from transbridge.ui.coordinators.project_coordinator import ProjectCoordinator
from transbridge.ui.shell.assistant_navigation import assistant_navigation

_APP = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.002)
    _APP.processEvents()
    assert predicate()


@pytest.fixture
def navigation(tmp_path, monkeypatch):
    monkeypatch.setattr(context_module.ParatranzConfig, "create_or_load", lambda: SimpleNamespace(token=""))
    source = tmp_path / "source.xml"
    source.write_bytes(Path("tests/contracts/io/fixtures/eet-small.xml").read_bytes())
    identities = count()
    services = build_persistence_v2_services(
        tmp_path / "data",
        id_factory=lambda: str(next(identities)),
        timestamp_factory=lambda: "2026-10-19T00:00:00+00:00",
    )
    runtime_context = RequestContext("assistant-navigation-test", run_id="navigation")
    source_request = ProjectSourceRequest(str(source), FormatId.XML_EET)
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Target", source=source_request), runtime_context
    )
    assert result.is_success, result.diagnostics
    target_active = services.project_lifecycle.active
    target = replace(
        runtime_context,
        project_id=target_active.project_ref.identity.value,
        variant_id=target_active.formal_variant_ref.identity.value,
    )
    result = services.gui_project_commands.create_variant("Alternative", runtime_context, copy_active=True)
    assert result.is_success, result.diagnostics
    target_alternative = services.project_lifecycle.active.formal_variant_ref
    result = services.gui_project_commands.create_project(
        ProjectProvisioningRequest("Old", source=source_request), runtime_context
    )
    assert result.is_success, result.diagnostics
    old = services.project_lifecycle.active
    context = context_module.AppContext(
        project_projection=services.project_projection,
        project_commands=services.gui_project_commands,
        runtime_context=runtime_context,
    )
    dependencies = {"project_projection": services.project_projection, "persistence_v2": services}
    host = SimpleNamespace(
        context=context,
        runtime_context=runtime_context,
        project_commands=services.gui_project_commands,
        current_project_opener=services.current_project_opener,
        app_runtime=SimpleNamespace(
            use_cases=SimpleNamespace(resolve=dependencies.__getitem__, names=lambda: tuple(dependencies))
        ),
        project_open_worker=None,
        save_worker=None,
        foreground_worker=None,
        workers=[],
        close_pending=False,
        workbench=SimpleNamespace(
            show_step2_progress=lambda *_: None,
            hide_step2_progress=lambda: None,
            setEnabled=lambda _: None,
        ),
        messages=[],
        shown=[],
        warnings=[],
    )
    host.show_message = host.messages.append
    host.show_workbench = lambda: host.shown.append(context.active_version_identity)
    host.project_coordinator = ProjectCoordinator(host)
    monkeypatch.setattr(QMessageBox, "warning", lambda _host, title, message: host.warnings.append((title, message)))
    ports = assistant_navigation(host, lambda: None)
    yield SimpleNamespace(
        host=host,
        services=services,
        context=context,
        target=target,
        target_alternative=target_alternative,
        old=old,
        ports=ports,
        switch=ports["switch_project"],
    )
    for worker in host.workers:
        worker.wait(5000)
    _APP.processEvents()
    context.close_projection()
    services.close()


def test_switch_prepares_exact_variant_off_gui_thread_and_completes_after_install(navigation, monkeypatch):
    case = navigation
    preparation_threads, completions = [], []
    prepare = case.host.current_project_opener.prepare_identity

    def capture_prepare(*args):
        preparation_threads.append(QThread.currentThread())
        return prepare(*args)

    monkeypatch.setattr(case.host.current_project_opener, "prepare_identity", capture_prepare)
    case.switch(case.target, lambda ok: completions.append((ok, case.context.active_version_identity)))
    assert not completions
    _wait_until(lambda: bool(completions))

    expected = (case.target.project_id, case.target.variant_id)
    assert completions == [(True, expected)], case.host.warnings
    assert case.context.active_version_identity == expected
    assert case.services.project_lifecycle.active.formal_variant_ref != case.target_alternative
    assert case.context.collection is not None and len(case.context.collection) > 0
    assert preparation_threads and preparation_threads[0] is not _APP.thread()
    assert case.host.shown == [expected]


def test_same_project_switches_to_exact_variant(navigation):
    case = navigation
    result = case.services.gui_project_commands.create_variant("Second", case.host.runtime_context, copy_active=True)
    assert result.is_success, result.diagnostics
    _APP.processEvents()
    target = replace(
        case.target,
        project_id=case.old.project_ref.identity.value,
        variant_id=case.old.formal_variant_ref.identity.value,
    )
    outcomes = []
    assert case.context.active_version_identity != (target.project_id, target.variant_id)
    case.switch(target, outcomes.append)
    _wait_until(lambda: bool(outcomes))
    assert outcomes == [True], case.host.warnings
    assert case.context.active_version_identity == (target.project_id, target.variant_id)


def test_switch_uses_target_context_even_if_shell_context_has_previous_binding(navigation):
    case = navigation
    case.host.runtime_context = replace(
        case.host.runtime_context,
        project_id=case.old.project_ref.identity.value,
        variant_id=case.old.formal_variant_ref.identity.value,
    )
    outcomes = []
    case.switch(case.target, outcomes.append)
    _wait_until(lambda: bool(outcomes))
    assert outcomes == [True], case.host.warnings
    assert case.context.active_version_identity == (case.target.project_id, case.target.variant_id)


def _make_dirty(case):
    entry = case.services.project_lifecycle.active.variant.snapshot().entries[0]
    result = case.services.gui_project_commands.update_entry(
        entry.entry_key, case.host.runtime_context, translation="unsaved edit"
    )
    assert result.is_success, result.diagnostics
    _APP.processEvents()
    assert case.context.dirty
    return entry.entry_key


def test_dirty_cancel_retains_original_project_and_never_starts_worker(navigation, monkeypatch):
    case = navigation
    _make_dirty(case)
    before = case.context.active_version_identity
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Cancel)
    outcomes = []
    case.switch(case.target, outcomes.append)
    assert outcomes == [False]
    assert case.context.active_version_identity == before
    assert case.context.dirty and not case.host.workers


@pytest.mark.parametrize("save", [True, False])
def test_dirty_save_or_discard_reuses_project_lifecycle_decision(navigation, monkeypatch, save):
    case = navigation
    key = _make_dirty(case)
    answer = QMessageBox.StandardButton.Yes if save else QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "question", lambda *_: answer)
    outcomes = []
    case.switch(case.target, outcomes.append)
    _wait_until(lambda: bool(outcomes))
    assert outcomes == [True]
    persisted = case.services.variants.read_snapshot(case.old.formal_variant_ref)
    entry = next(item for item in persisted.envelope.data["entries"] if item["entry_key"] == key.to_dict())
    assert (entry["translation"] == "unsaved edit") is save


@pytest.mark.parametrize("variant", ["missing", "other-project"])
def test_missing_or_foreign_variant_preserves_current_project(navigation, variant):
    case = navigation
    target = replace(
        case.target,
        variant_id="missing" if variant == "missing" else case.old.formal_variant_ref.identity.value,
    )
    before = case.context.active_version_identity
    pointer = Path(case.services.root) / "active-project.json"
    previous_pointer = pointer.read_bytes()
    outcomes = []
    case.switch(target, outcomes.append)
    _wait_until(lambda: bool(outcomes))
    assert outcomes == [False]
    assert case.context.active_version_identity == before
    assert pointer.read_bytes() == previous_pointer
    assert case.services.project_lifecycle.active is case.old
    assert not case.host.shown
    assert case.host.warnings


def test_duplicate_switch_is_rejected_while_preparation_is_running(navigation, monkeypatch):
    case = navigation
    entered, release = Event(), Event()
    prepare = case.host.current_project_opener.prepare_identity

    def blocking_prepare(*args):
        entered.set()
        assert release.wait(5)
        return prepare(*args)

    monkeypatch.setattr(case.host.current_project_opener, "prepare_identity", blocking_prepare)
    first, second = [], []
    try:
        case.switch(case.target, first.append)
        assert entered.wait(2)
        case.switch(case.target, second.append)
        assert not first and second == [False]
        assert case.host.messages
    finally:
        release.set()
    _wait_until(lambda: bool(first))
    assert first == [True] and second == [False]


@pytest.mark.parametrize("worker", ["save_worker", "foreground_worker", "project_open_worker", "ai_tasks"])
def test_busy_workbench_does_not_prompt_or_start_switch(navigation, monkeypatch, worker):
    case = navigation
    if worker == "ai_tasks":
        case.host.workbench.ai_tasks = SimpleNamespace(busy=True)
    else:
        setattr(case.host, worker, object())
    monkeypatch.setattr(QMessageBox, "question", lambda *_: pytest.fail("Busy switch must not prompt"))
    outcomes = []
    case.switch(case.target, outcomes.append)
    assert outcomes == [False]
    assert not case.host.workers
    assert case.services.project_lifecycle.active is case.old


def test_project_labels_read_metadata_without_loading_translations(navigation, monkeypatch):
    case = navigation
    monkeypatch.setattr(case.services.variants, "read_snapshot", lambda *_: pytest.fail("Loaded translation data"))
    labels = case.ports["session_project_labels"](case.target.project_id, case.target.variant_id)
    assert labels == {"project_name": "Target", "variant_name": "默认", "available": True, "reason": None}
    assert not case.ports["session_project_labels"](case.target.project_id, "missing")["available"]
    assert not case.ports["session_project_labels"](None, None)["available"]


@pytest.mark.parametrize("missing", ["project", "variant"])
def test_labels_keep_history_available_but_report_missing_binding(navigation, missing):
    case = navigation
    project_ref = ProjectRef(ProjectId(case.target.project_id))
    variant_ref = VariantRef(VariantId(case.target.variant_id), project_ref.identity)
    path = (
        case.services.projects.path_for(project_ref)
        if missing == "project"
        else case.services.variants.path_for(variant_ref)
    )
    Path(path).unlink()
    labels = case.ports["session_project_labels"](case.target.project_id, case.target.variant_id)
    assert not labels["available"] and labels["reason"]


def test_success_callback_requires_installed_workbench_to_match_binding(navigation, monkeypatch):
    case = navigation

    def wrong_install(_prepare, **kwargs):
        kwargs["on_success"]({})
        return True

    monkeypatch.setattr(case.host.project_coordinator, "_start_current_project_open", wrong_install)
    outcomes = []
    case.switch(case.target, outcomes.append)
    assert outcomes == [False]
