from dataclasses import replace
import time
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton
import pytest

from transbridge.application.contracts import RequestContext
from transbridge.application.io import EntryKey, SourceNamespace
from transbridge.application.projects import ProjectProvisioningRequest
from transbridge.bootstrap import build_runtime
from transbridge.paratranz.config_manager import ParatranzConfig
from transbridge.smart_assistant.tools.translation_task_results import result_view
from transbridge.ui.context import AppContext
from transbridge.ui.tools.ai_translator.task_registry import AiTaskRegistry


def _wait_until(app, condition):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        QTest.qWait(5)
    raise AssertionError("The task history operation did not finish")


def test_desktop_history_displays_and_exports_a_result_after_its_project_is_deleted(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(ParatranzConfig, "create_or_load", lambda: ParatranzConfig())
    app = QApplication.instance() or QApplication([])
    runtime = build_runtime({"persistence_v2_root": tmp_path / "data", "ui_config_path": tmp_path / "ui.json"})
    use_cases = runtime.use_cases
    commands = use_cases.resolve("gui_project_commands")
    task_store = use_cases.resolve("task_store")
    context = RequestContext("gui", run_id="project-deletion")
    app_context = None
    registry = None
    try:
        created = commands.create_project(ProjectProvisioningRequest("Previous project"), context)
        assert created.is_success, created.diagnostics
        project_id, variant_id = created.value["project_id"], created.value["variant_id"]
        run_id = "orphaned-download-result"
        task_context = RequestContext("result-owner", session_id="result-session")
        recovery = task_store.recovery(owner_id=task_context.owner_id, session_id=task_context.session_id)
        recovery.create({
            "task_id": run_id,
            "project_id": project_id,
            "variant_id": variant_id,
            "mode": "download",
            "config_digest": "synthetic",
            "state": "completed",
            "created_at": "2026-11-09T00:00:00+08:00",
            "sources": [],
            "result_kind": "download",
            "results_only": True,
            "origin": "smart-assistant",
        })
        key = EntryKey(SourceNamespace("source:download"), "row")
        candidate = {
            "entry_key": key.to_dict(),
            "source": "Downloaded strings",
            "action": "translation",
            "before": {"original": "Greeting", "translation": "", "stage": 0, "context": "NPC_:FULL", "revision": 0},
            "status": "succeeded",
            "text": "独立成果",
            "stage": 1,
            "decision": "pending",
            "applied": False,
            "attempt": 1,
        }
        recovery.save_results(run_id, [candidate])
        removed = use_cases.resolve("project_management").delete(project_id, context, expected_name="Previous project")
        assert removed.is_success, removed.diagnostics
        app_context = AppContext(
            project_projection=use_cases.resolve("project_projection"),
            project_commands=commands,
            project_catalog=use_cases.resolve("project_catalog"),
            task_store=task_store,
        )
        assert app_context.active_version_identity is None
        registry = AiTaskRegistry(app_context)
        registry.open_history(run_id=run_id)
        (history,) = registry.history_windows
        _wait_until(app, lambda: not history.busy and bool(history._dialogs))
        assert history.tasks.currentItem().data(0, Qt.ItemDataRole.UserRole) == run_id
        assert "原项目已删除" in history.tasks.currentItem().text(3)

        (result,) = history._dialogs
        _wait_until(app, lambda: result.model.rowCount() == 1 and not result.busy)
        assert result.model.data(result.model.index(0, 3)) == "独立成果"
        assert "原项目已删除" in result.project_status.text()
        assert not any("恢复译文" in button.text() for button in result.findChildren(QPushButton))
        exported = tmp_path / "result.csv"
        result.export(str(exported), "csv")
        _wait_until(app, lambda: not result.busy)
        assert "独立成果" in exported.read_text(encoding="utf-8-sig")
        assert not use_cases.resolve("project_catalog").list_projects().projects

        assistant = SimpleNamespace(app_context=app_context, request_context=task_context)
        (available,) = result_view(assistant)["results"]
        assert available["result_id"] == run_id and available["target_unavailable"]
        detached = result_view(assistant, run_id)
        assert detached["target_unavailable"]
        assert detached["entries"][0]["translation"] == "独立成果"
        assistant.request_context = replace(task_context, session_id="different-session")
        assert result_view(assistant)["results"] == []
        with pytest.raises(ValueError):
            result_view(assistant, run_id)
    finally:
        if registry is not None:
            registry.shutdown()
            for history in registry.history_windows:
                for dialog in history._dialogs:
                    dialog.close()
                history.close()
        if app_context is not None:
            app_context.close_projection()
        assert runtime.close().is_success
