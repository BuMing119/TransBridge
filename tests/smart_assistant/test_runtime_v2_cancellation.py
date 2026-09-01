from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from tests.smart_assistant.tools import test_source_import_authority as source_fixtures
from transbridge.bootstrap.runtime import UseCaseRegistry
from transbridge.config.llm import LLMConfig
from transbridge.smart_assistant.tools.task_manager import TaskManager
from transbridge.smart_assistant.tools.tool_proofreader import _tool_run_postprocess
from transbridge.smart_assistant.tools.types import ExecutionContext
from transbridge.ui.shell.task_center import TaskCenterController, TaskCenterPanel

project = source_fixtures.project


@pytest.fixture
def manager():
    TaskManager.reset()
    yield TaskManager()
    TaskManager.reset()


def test_task_center_cancel_preserves_real_variant_and_saved_files(project, manager, monkeypatch):
    source = project.source.read_text(encoding="utf-8")
    project.source.write_text(
        source.replace("<TRADUIT></TRADUIT>", "<TRADUIT>before</TRADUIT>").replace(
            "<STATUS>0</STATUS>", "<STATUS>1</STATUS>"
        ),
        encoding="utf-8",
    )
    source_fixtures.load_source(project)
    commands = project.services.gui_project_commands
    assert commands.save(project.request).is_success
    project.app.processEvents()
    active = project.services.project_lifecycle.active
    before = active.variant.snapshot()
    variant_path = Path(project.services.variants.path_for(active.formal_variant_ref))
    project_path = Path(project.services.projects.path_for(active.project_ref))
    saved_before = (project_path.read_bytes(), variant_path.read_bytes())
    entered = threading.Event()
    release = threading.Event()

    class Provider:
        def chat(self, messages, max_tokens=0):
            entered.set()
            assert release.wait(5)
            payload = json.loads(messages[-1]["content"])
            return json.dumps({
                "results": [
                    {"entry_key": item["entry_key"], "final_translation": "late correction"}
                    for item in payload["entries"]
                ]
            })

    monkeypatch.setattr(
        "transbridge.smart_assistant.tools._common.load_llm_config",
        lambda: LLMConfig(api_key="fixture", model="fixture"),
    )
    monkeypatch.setattr("transbridge.infra.llm_client.create_llm_client", lambda *_args: Provider())
    monkeypatch.setattr(
        "transbridge.ai_translator.term_database.TermDatabaseManager",
        lambda **_kwargs: SimpleNamespace(load_all=lambda: None, match_terms=lambda _texts: {}),
    )
    monkeypatch.setattr("transbridge.paratranz.config_manager.ParatranzConfig.get_data_dir", lambda: str(project.root))
    monkeypatch.setattr(
        "transbridge.smart_assistant.tools.tool_proofreader._resolve_report_directory",
        lambda _ctx: project.root / "reports",
    )
    catalog = SimpleNamespace(list=lambda *_args, **_kwargs: ())
    runtime = SimpleNamespace(
        tasks=manager.runtime,
        use_cases=UseCaseRegistry({"task_history": catalog, "task_recovery": catalog}),
    )
    panel = TaskCenterPanel()
    controller = TaskCenterController(runtime, project.request, panel)
    controller.start()
    handle = None
    try:
        result = _tool_run_postprocess({"scope": "all"}, ExecutionContext(app_context=project.ctx))
        assert result.success, result.message
        task_id = result.data["task_id"]
        handle = manager.get_handle(task_id)
        assert entered.wait(5)
        project.app.processEvents()
        panel._current.setCurrentRow(0)
        assert panel._cancel.isEnabled()
        panel._cancel.click()
        assert manager.get_status(task_id)["status"] == "cancelling"
        assert handle.stop_event.is_set()
        release.set()
        deadline = time.monotonic() + 5
        while handle._thread.is_alive() and time.monotonic() < deadline:
            project.app.processEvents()
            handle._thread.join(0.01)
        assert not handle._thread.is_alive()
        assert manager.get_status(task_id)["status"] == "cancelled"
        assert active.variant.snapshot() == before
        assert not project.ctx.authoritative_projection_diverged()
        assert next(iter(project.ctx.collection)).translation == "before"
        assert commands.save(project.request).is_success
        assert (project_path.read_bytes(), variant_path.read_bytes()) == saved_before
    finally:
        release.set()
        controller.close()
        panel.deleteLater()
        if handle is not None:
            deadline = time.monotonic() + 5
            while handle._thread.is_alive() and time.monotonic() < deadline:
                project.app.processEvents()
                handle._thread.join(0.01)
