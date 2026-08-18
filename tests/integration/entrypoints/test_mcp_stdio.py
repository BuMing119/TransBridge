"""S05 real-process contracts for the independent MCP stdio entry point."""

from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from transbridge.application.contracts import OperationOutcome, OperationResult
from transbridge.bootstrap import bind_runtime, build_runtime
from transbridge.entrypoints.agent import invoke_operation as invoke_agent_operation
from transbridge.entrypoints.cli import invoke_operation as invoke_cli_operation
from transbridge.entrypoints.gui import invoke_operation as invoke_gui_operation
from transbridge.entrypoints.headless import build_headless_binding
from transbridge.smart_assistant.mcp import MCPAdapter, MCPServer
from transbridge.smart_assistant.tool_registry import ToolSpec

PROJECT_ROOT = Path(__file__).parents[3]
PROTOCOL_VERSION = "2025-06-18"


def _request(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def _run_mcp(
    messages: list[dict[str, Any]],
    *,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
    command: list[str] | None = None,
    inject_source: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    environment = os.environ.copy()
    if inject_source:
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(PROJECT_ROOT / "src"), existing_pythonpath) if value
        )
    else:
        environment.pop("PYTHONPATH", None)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment.update(extra_env or {})
    wire_input = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
    completed = subprocess.run(
        command or [sys.executable, "-m", "transbridge.entrypoints.mcp"],
        input=wire_input,
        cwd=cwd,
        env=environment,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    return completed, responses


def _initialize_sequence() -> list[dict[str, Any]]:
    return [
        _request(
            1,
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "TransBridge contract test", "version": "1"},
            },
        ),
        _notification("notifications/initialized"),
    ]


def test_real_stdio_process_negotiates_lists_calls_and_shuts_down(tmp_path: Path) -> None:
    unicode_cwd = tmp_path / "工作目录-入口"
    unicode_cwd.mkdir()
    canary = "api_key=supersecretvalue"
    messages = _initialize_sequence() + [
        _request(2, "tools/list", {}),
        _request(3, "tools/call", {"name": "transbridge.capabilities", "arguments": {}}),
        _request(4, "tools/call", {"name": "transbridge.project-context", "arguments": {}}),
        _request(5, "shutdown", {}),
    ]

    completed, responses = _run_mcp(
        messages,
        cwd=unicode_cwd,
        extra_env={"TRANSBRIDGE_SECRET_LLM_API_KEY": canary},
    )

    assert completed.returncode == 0, completed.stderr
    assert len(responses) == 5
    assert all(response["jsonrpc"] == "2.0" for response in responses)
    assert responses[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert responses[0]["result"]["capabilities"]["tools"]["listChanged"] is False
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert names == {"transbridge.capabilities", "transbridge.project-context"}

    capability_result = OperationResult.from_dict(responses[2]["result"]["structuredContent"])
    project_result = OperationResult.from_dict(responses[3]["result"]["structuredContent"])
    assert capability_result.outcome is OperationOutcome.COMPLETED
    assert project_result.outcome is OperationOutcome.FAILED
    assert project_result.diagnostics[0].code == "PROJECT_CONTEXT_REQUIRED"
    assert responses[3]["result"]["isError"] is True
    assert responses[4]["result"] is None
    assert canary not in completed.stdout
    assert canary not in completed.stderr


def test_installed_console_script_real_stdio_smoke(tmp_path: Path) -> None:
    executable_name = "transbridge-mcp.exe" if sys.platform == "win32" else "transbridge-mcp"
    adjacent = Path(sys.executable).with_name(executable_name)
    executable = adjacent if adjacent.is_file() else Path(shutil.which(executable_name) or "")
    assert executable.is_file(), f"installed console script is missing: {adjacent}"
    unicode_cwd = tmp_path / "安装态-stdio"
    unicode_cwd.mkdir()
    messages = _initialize_sequence() + [
        _request(2, "tools/list", {}),
        _request(3, "tools/call", {"name": "transbridge.capabilities", "arguments": {}}),
        _request(4, "shutdown", {}),
    ]

    completed, responses = _run_mcp(
        messages,
        cwd=unicode_cwd,
        command=[str(executable)],
        inject_source=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert [response["id"] for response in responses] == [1, 2, 3, 4]
    assert responses[1]["result"]["tools"]
    result = OperationResult.from_dict(responses[2]["result"]["structuredContent"])
    assert result.outcome is OperationOutcome.COMPLETED


def test_real_stdio_process_closes_cleanly_on_eof(tmp_path: Path) -> None:
    unicode_cwd = tmp_path / "仅EOF"
    unicode_cwd.mkdir()

    completed, responses = _run_mcp(_initialize_sequence(), cwd=unicode_cwd)

    assert completed.returncode == 0, completed.stderr
    assert [response["id"] for response in responses] == [1]


def test_primary_lifecycle_rejects_calls_before_initialized(tmp_path: Path) -> None:
    messages = [
        _initialize_sequence()[0],
        _request(2, "tools/list", {}),
        _notification("notifications/initialized"),
        _request(3, "tools/list", {}),
        _request(4, "shutdown", {}),
    ]

    completed, responses = _run_mcp(messages, cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert [response["id"] for response in responses] == [1, 2, 3, 4]
    assert responses[1]["error"] == {"code": -32002, "message": "Server is not initialized"}
    assert responses[2]["result"]["tools"]


def test_real_stdio_process_keeps_protocol_errors_structured(tmp_path: Path) -> None:
    messages = _initialize_sequence() + [
        _notification("notifications/initialized"),
        _notification("api_key=supersecretvalue", {}),
        _request(2, "initialize", {"protocolVersion": PROTOCOL_VERSION}),
        _request(3, "tools/call", {"name": "transbridge.capabilities", "arguments": []}),
        _request(4, "tools/call", {"name": "api_key=supersecretvalue", "arguments": {}}),
        _request(5, "api_key=supersecretvalue", {}),
        _request(6, "shutdown", {}),
    ]

    completed, responses = _run_mcp(messages, cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert [response["id"] for response in responses] == [1, 2, 3, 4, 5, 6]
    assert responses[1]["error"]["code"] == -32602
    assert responses[2]["error"]["code"] == -32602
    assert responses[3]["error"]["code"] == -32602
    assert responses[3]["error"]["message"] == "unknown tool: ***REDACTED***"
    assert responses[4]["error"] == {"code": -32601, "message": "Method not found"}
    assert "supersecretvalue" not in completed.stdout


def test_unsupported_protocol_negotiation_is_deterministic(tmp_path: Path) -> None:
    messages = [
        _request(1, "initialize", {"protocolVersion": "2099-01-01", "capabilities": {}}),
        _notification("notifications/initialized"),
        _request(2, "shutdown", {}),
    ]

    completed, responses = _run_mcp(messages, cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert responses[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert responses[1]["result"] is None


def test_non_finite_json_and_container_request_id_are_rejected_safely(tmp_path: Path) -> None:
    messages = [
        _request(1, "initialize", {"protocolVersion": float("nan")}),
        *_initialize_sequence(),
        {"jsonrpc": "2.0", "id": [], "method": "tools/list", "params": {}},
        _request(2, "shutdown", {}),
    ]

    completed, responses = _run_mcp(messages, cwd=tmp_path)

    assert completed.returncode == 0, completed.stderr
    assert responses[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "Parse error"},
    }
    assert responses[2] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Invalid Request"},
    }
    assert responses[3]["result"] is None
    assert "ValueError" not in completed.stderr
    assert all(json.loads(line) for line in completed.stdout.splitlines())


def test_project_context_can_be_injected_without_gui_state() -> None:
    binding = build_headless_binding("mcp", environ={}, project_id="project-1")
    try:
        payload = MCPAdapter(binding=binding).call_tool("transbridge.project-context", {})
        result = OperationResult.from_dict(payload["structuredContent"])
    finally:
        binding.runtime.close()

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value["context"]["project_id"] == "project-1"
    assert result.value["context"]["owner_id"] == "mcp:stdio"


def test_stateless_and_context_required_operations_have_entrypoint_parity() -> None:
    outcomes: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {}
    runtime = build_runtime()
    adapters = {
        "gui": (bind_runtime(runtime, "gui"), invoke_gui_operation),
        "agent": (bind_runtime(runtime, "agent"), invoke_agent_operation),
        "cli": (bind_runtime(runtime, "cli"), invoke_cli_operation),
    }
    mcp_binding = bind_runtime(runtime, "mcp:stdio")
    try:
        for entrypoint, (binding, invoke) in adapters.items():
            stateless = invoke(binding, "capabilities")
            contextual = invoke(binding, "project-context")
            outcomes[entrypoint] = _parity_signature(stateless, contextual)

        mcp_adapter = MCPAdapter(binding=mcp_binding)
        mcp_stateless = OperationResult.from_dict(
            mcp_adapter.call_tool("transbridge.capabilities", {})["structuredContent"]
        )
        mcp_contextual = OperationResult.from_dict(
            mcp_adapter.call_tool("transbridge.project-context", {})["structuredContent"]
        )
        outcomes["mcp"] = _parity_signature(mcp_stateless, mcp_contextual)
    finally:
        runtime.close()

    assert set(outcomes.values()) == {("completed", "PROJECT_CONTEXT_REQUIRED", (), ())}


def _parity_signature(
    stateless: OperationResult[Any],
    contextual: OperationResult[Any],
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    return (
        stateless.outcome.value,
        contextual.diagnostics[0].code,
        stateless.artifact_refs,
        contextual.artifact_refs,
    )


def test_legacy_mcp_import_facade_preserves_canonical_schema() -> None:
    canonical_schema = {
        "type": "object",
        "properties": {"count": {"type": "integer", "minimum": 1}},
        "required": ["count"],
        "additionalProperties": False,
    }
    spec = ToolSpec(
        name="read_only",
        display_name="Read only",
        description="Compatibility tool",
        parameters=canonical_schema,
    )

    class Registry:
        @staticmethod
        def list_all() -> list[ToolSpec]:
            return [spec]

        @staticmethod
        def get(name: str, namespace: str | None = None) -> ToolSpec | None:
            del namespace
            return spec if name == spec.name else None

    adapter = MCPAdapter(Registry)
    server = MCPServer(Registry, adapter=adapter)

    assert adapter.list_tools()[0]["inputSchema"] == canonical_schema
    assert server._strict_lifecycle is False


def test_shutdown_and_eof_close_the_bound_runtime_exactly_once() -> None:
    binding = build_headless_binding("mcp", environ={})
    close_calls = 0

    def close_runtime() -> None:
        nonlocal close_calls
        close_calls += 1
        binding.runtime.close()

    server = MCPServer(adapter=MCPAdapter(binding=binding), on_close=close_runtime)
    messages = _initialize_sequence() + [_request(2, "shutdown", {})]
    source = StringIO("".join(json.dumps(message) + "\n" for message in messages))
    output = StringIO()

    server.run_stdio(source, output)
    server.close()

    assert close_calls == 1
    assert binding.runtime.closed is True
    assert [json.loads(line)["id"] for line in output.getvalue().splitlines()] == [1, 2]


def test_primary_mcp_entrypoint_does_not_register_legacy_gui_tools() -> None:
    source = (PROJECT_ROOT / "src" / "transbridge" / "entrypoints" / "mcp.py").read_text(encoding="utf-8")

    assert "register_all" not in source
    assert "ToolRegistry" not in source
    assert "transbridge.ui" not in source
