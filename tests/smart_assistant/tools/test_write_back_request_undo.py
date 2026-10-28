"""Assistant request admission stores and reverses real batch BSA file receipts."""

from dataclasses import replace
from pathlib import Path

from tests.contracts.io.test_localized_hydrated_publication import localized as localized_fixture
from tests.smart_assistant.tools.test_write_back_options import _result, _slot, sources as sources_fixture
from transbridge.application.assistant_requests.models import RequestStatus
from transbridge.application.io import FormatId
from transbridge.smart_assistant.request_execution import RequestExecutionGate
from transbridge.smart_assistant.tool_registry import ToolRegistry
from transbridge.smart_assistant.tools import tool_writer
from transbridge.smart_assistant.tools.base import execute_with_guardrails

pytest_plugins = ["tests.smart_assistant.test_request_execution"]
sources = sources_fixture
localized = localized_fixture


def test_admitted_batch_records_bsa_and_xml_in_one_session_undo_commit(setup, sources, localized):
    service, context, previous, ctx, manager = setup
    service.scheduler.release(previous.admission)
    tool_writer._register_writer_tools()
    selected = service.scheduler.select_next_turn(
        context.session_id, "view", (previous.current_request(),), allowed_tools=("write_back",)
    )
    gate = RequestExecutionGate(service, context, selected.admission)
    ctx.assistant_gate = gate
    ctx.request_context = replace(context, authorized_roots=(str(sources.root),))
    ctx.app_context = sources.ctx
    sources.ctx.slots["localized-key"] = _slot(localized.source, FormatId.PLUGIN_SSE, ctx.request_context)
    output = sources.root / "published"
    output.mkdir()
    plugin, archive = output / "localized.esp", output / "localized.bsa"
    plugin.write_bytes(b"old plugin")
    archive.write_bytes(b"old archive")
    service.undo.file_backup_root = sources.root / "undo-backups"

    started = execute_with_guardrails(
        ToolRegistry.get("write_back"),
        {
            "target": "auto",
            "source_keys": ["localized-key", "xml-key"],
            "output_dir": str(output),
            "pack_bsa": True,
            "overwrite": True,
        },
        ctx,
        middlewares=[],
    )
    result = _result(manager, started)
    request = gate.current_request()
    commits = service.state(context)["undo_rounds"][request.work_round_id]["commits"]
    assert len(commits) == 1 and commits[0]["status"] == "recorded"
    assert set(commits[0]["targets"]) == set(result["artifacts"])
    assert set(map(Path, commits[0]["targets"])) == {plugin, archive, output / "second.xml"}
    service.update_request(context, request.request_id, lambda item: replace(item, status=RequestStatus.CANCELLED))

    outcome = service.undo.undo(context, request.work_round_id)

    assert not outcome["partial"], outcome
    assert plugin.read_bytes() == b"old plugin"
    assert archive.read_bytes() == b"old archive"
    assert not (output / "second.xml").exists()
