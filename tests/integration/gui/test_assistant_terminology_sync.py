from dataclasses import replace
from types import SimpleNamespace

from tests.contracts.config.test_unified_repository import _repository
from tests.integration.bootstrap.test_terminology_task_wiring import _create_project_context
from tests.persistence.terminology.test_inbound_review import _change_set, _line
from transbridge.bootstrap import build_runtime
from transbridge.config.llm import LLMConfig
from transbridge.config.paratranz import ParatranzConfig
from transbridge.smart_assistant.graph_executor import GraphExecutor
from transbridge.smart_assistant.tool_execution_handler import ToolExecutionHandler
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_terminology_sync
from transbridge.ui.app import create_workbench_context
from transbridge.ui.tools.terminology.presenter import TerminologyUiServices


def test_confirmed_assistant_review_uses_workbench_factory_and_session_scoped_proposal(tmp_path, monkeypatch):
    config = _repository(tmp_path / "settings")
    monkeypatch.setattr(LLMConfig, "load_from_file", LLMConfig)
    monkeypatch.setattr(
        ParatranzConfig,
        "create_or_load",
        lambda: ParatranzConfig(
            repository=config, config_path=config.path, credential_store=config.credential_store, environment={}
        ),
    )
    runtime = build_runtime(settings={"persistence_v2_root": tmp_path})
    try:
        bound = _create_project_context(runtime, "Terminology")
        request = replace(bound, permissions=frozenset({"gui"}), session_id="chat-A", metadata=(("entrypoint", "gui"),))
        app = create_workbench_context(runtime, request)
        desktop = TerminologyUiServices.from_runtime(runtime, request)
        line, profile = _line(bound.project_id, bound.variant_id)
        desktop.queries.sync_state.activate_line(line, profile)
        changes = desktop.queries.inbound_reviews.save_change_set(_change_set(line))
        item = changes.items[0]
        decisions = iter([False, "switch", True, True])

        def confirm(*_args):
            nonlocal request
            decision = next(decisions)
            if decision == "switch":
                request = replace(request, session_id="chat-B")
                return True
            return decision

        reports = []
        handler = ToolExecutionHandler(
            app,
            SimpleNamespace(add_observation=lambda *_: None),
            request_context_provider=lambda: request,
            on_confirm_permission=confirm,
            on_tool_result=lambda _, result: reports.append(result),
        )
        ctx = handler.build_execution_context()
        assert tool_terminology_sync._service(ctx) is desktop.sync
        listing = handler.execute_step({"tool": "terminology_sync_inbound", "args": {}})
        assert listing.success and listing.data["change_sets"][0]["change_set_id"] == changes.change_set_id
        preview = {
            "tool": "terminology_sync_inbound_preview",
            "args": {
                "change_set_id": changes.change_set_id,
                "choices": [{"item_id": item.item_id, "decision": "edit", "translation": "已复核译文"}],
            },
        }
        result = handler.execute_step(preview)
        assert result.success, result.message
        commit = {
            "tool": "terminology_sync_inbound_commit",
            "args": {"proposal_digest": result.data["proposal_digest"]},
        }
        handler.execute_step(commit)
        assert reports[-1].error_code == "CONFIRMATION_DENIED"
        assert desktop.queries.active_draft(bound.project_id, bound.variant_id) is None
        handler.execute_step(commit)
        assert reports[-1].error_code == "CONFIRMATION_SCOPE_CHANGED"
        assert not handler.execute_step(commit).success
        assert desktop.queries.active_draft(bound.project_id, bound.variant_id) is None
        request = replace(request, session_id="chat-A")
        assert handler.execute_step(commit).success
        saved = desktop.queries.active_draft(bound.project_id, bound.variant_id)
        assert saved.decisions[0].translation == "已复核译文"
        assert saved.actions[0].actor == request.owner_id
        assert desktop.queries.effective_version(bound.project_id, bound.variant_id) is None
        assert "manual_actor_id" not in dict(request.metadata)
        preview["args"]["choices"] = [{"item_id": changes.items[1].item_id, "decision": "accept"}]
        remaining = handler.execute_step(preview)
        graph = GraphExecutor(ToolRegistry, handler.build_execution_context(), checkpoint_manager=object())
        try:
            graph.on_step_requires_confirmation(lambda node, *_: graph.provide_decision(node, "继续"))
            step = {"id": 1, "tool": commit["tool"], "args": {"proposal_digest": remaining.data["proposal_digest"]}}
            outcome = graph._run_single(step)
            assert outcome.success, outcome.message
            assert len(desktop.queries.active_draft(bound.project_id, bound.variant_id).decisions) == 2
        finally:
            graph.shutdown()
    finally:
        runtime.close()
