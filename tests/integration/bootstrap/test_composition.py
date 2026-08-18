"""S03 integration contracts for the process Composition Root."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys

import pytest

from transbridge.application.capabilities import (
    CapabilityId,
    CapabilityRegistry,
    CapabilityReport,
    CapabilityState,
)
from transbridge.application.contracts import ErrorCategory, OperationOutcome
from transbridge.bootstrap import RuntimePorts, bind_runtime, build_runtime


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 18, tzinfo=UTC)


class _Ids:
    def __init__(self) -> None:
        self._next = 0

    def new_id(self) -> str:
        self._next += 1
        return f"run-{self._next}"


class _Secrets:
    def __init__(self, names: set[str] | None = None) -> None:
        self.names = set(names or ())

    def has_secret(self, name, context) -> bool:
        del context
        return name in self.names

    def get_secret(self, name, context) -> str | None:
        del context
        return "present-but-never-serialized" if name in self.names else None


class _Security:
    def authorize(self, context, action, resource=None) -> bool:
        del resource
        return action in context.permissions


def _ports(secret_names: set[str] | None = None) -> RuntimePorts:
    return RuntimePorts(_Clock(), _Ids(), _Secrets(secret_names), _Security())


def test_build_runtime_is_headless_in_fresh_process() -> None:
    project_root = Path(__file__).parents[3]
    script = (
        f"import sys; sys.path.insert(0, {str(project_root / 'src')!r}); "
        "from transbridge.bootstrap import build_runtime; "
        "runtime = build_runtime(); "
        "assert not any(name == 'PyQt6' or name.startswith('PyQt6.') for name in sys.modules); "
        "runtime.close()"
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_two_runtimes_do_not_share_mutable_state_or_capability_registry() -> None:
    source = CapabilityRegistry((CapabilityReport(CapabilityId("base"), CapabilityState.AVAILABLE),))
    first = build_runtime({"nested": {"value": 1}}, capabilities=source)
    second = build_runtime({"nested": {"value": 1}}, capabilities=source)

    first.state["owner"] = "first"
    first.capabilities.register(CapabilityReport(CapabilityId("first.only"), CapabilityState.AVAILABLE))
    with pytest.raises(TypeError):
        first.settings["nested"]["value"] = 2

    assert "owner" not in second.state
    assert second.capabilities.report("first.only").state is CapabilityState.UNAVAILABLE
    assert source.report("first.only").state is CapabilityState.UNAVAILABLE
    assert second.settings["nested"]["value"] == 1


def test_runtime_uses_injected_ports_and_generates_scoped_contexts() -> None:
    ports = _ports()
    runtime = build_runtime(ports=ports)

    first = runtime.context("gui", project_id="project-1")
    second = runtime.context("mcp")

    assert runtime.ports is ports
    assert first.owner_id == "gui"
    assert first.project_id == "project-1"
    assert first.run_id == "run-1"
    assert second.run_id == "run-2"


def test_missing_project_returns_structured_prerequisite_result() -> None:
    runtime = build_runtime(ports=_ports())
    context = runtime.context("agent")

    result = runtime.require_context(context, project=True)

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].category is ErrorCategory.PREREQUISITE
    assert result.diagnostics[0].code == "PROJECT_CONTEXT_REQUIRED"


def test_missing_runtime_context_returns_structured_prerequisite_result() -> None:
    runtime = build_runtime(ports=_ports())

    result = runtime.require_context(None)

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].category is ErrorCategory.PREREQUISITE
    assert result.diagnostics[0].code == "RUNTIME_CONTEXT_REQUIRED"


def test_missing_secret_returns_prerequisite_without_secret_value() -> None:
    runtime = build_runtime(ports=_ports())
    context = runtime.context("mcp", project_id="project-1")

    result = runtime.require_context(context, secrets=("llm.api_key",))

    assert result.outcome is OperationOutcome.FAILED
    assert result.diagnostics[0].category is ErrorCategory.PREREQUISITE
    assert result.diagnostics[0].code == "SECRET_REQUIRED"
    assert "present-but-never-serialized" not in str(result.to_dict())


def test_satisfied_context_and_secret_are_accepted() -> None:
    runtime = build_runtime(ports=_ports({"llm.api_key"}))
    context = runtime.context("gui", project_id="project-1")

    result = runtime.require_context(
        context,
        project=True,
        secrets=("llm.api_key",),
    )

    assert result.outcome is OperationOutcome.COMPLETED
    assert result.value["context"]["owner_id"] == "gui"


def test_close_is_reverse_order_idempotent_and_releases_after_failure() -> None:
    calls: list[str] = []

    class Resource:
        def __init__(self, name: str, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def close(self) -> None:
            calls.append(self.name)
            if self.fail:
                raise RuntimeError(f"private path for {self.name}")

    runtime = build_runtime(resources=(Resource("first"), Resource("second", fail=True), Resource("third")))

    first_result = runtime.close()
    second_result = runtime.close()

    assert calls == ["third", "second", "first"]
    assert first_result is second_result
    assert first_result.outcome is OperationOutcome.PARTIAL
    assert first_result.counts.succeeded == 2
    assert first_result.counts.failed == 1
    assert "private path" not in str(first_result.to_dict())
    assert runtime.closed is True


def test_entrypoint_binding_reuses_runtime_identity() -> None:
    runtime = build_runtime(ports=_ports())

    gui = bind_runtime(runtime, "gui", project_id="project-1")
    agent = bind_runtime(runtime, "agent", project_id="project-1")

    assert gui.runtime is runtime
    assert agent.runtime is runtime
    assert gui.context.owner_id == "gui"
    assert agent.context.owner_id == "agent"


def test_legacy_tool_wrappers_reject_missing_injected_context() -> None:
    from transbridge.smart_assistant.tools import ExecutionContext
    from transbridge.smart_assistant.tools.tool_editor import _tool_set_filters
    from transbridge.smart_assistant.tools.tool_proofreader import _tool_get_quality_report
    from transbridge.smart_assistant.tools.tool_translator import _tool_get_translation_config

    headless_legacy_context = ExecutionContext(app_context=None, task_manager=object())
    for result in (
        _tool_set_filters({}, None),
        _tool_get_quality_report({}, None),
        _tool_get_translation_config({}, None),
        _tool_get_quality_report({}, headless_legacy_context),
    ):
        assert result.success is False
        assert result.error_category == "config"
        assert result.error_code == "RUNTIME_CONTEXT_REQUIRED"

    projected_context = ExecutionContext(app_context=object(), task_manager=object())
    assert _tool_get_quality_report({}, projected_context).success is True


def test_legacy_controller_modules_no_longer_construct_hidden_context() -> None:
    project_root = Path(__file__).parents[3]
    tools = project_root / "src" / "transbridge" / "smart_assistant" / "tools"

    for filename in ("tool_editor.py", "tool_proofreader.py", "tool_translator.py"):
        source = (tools / filename).read_text(encoding="utf-8")
        assert "Controller(AppContext(), TaskManager())" not in source
        assert "from transbridge.ui.context import AppContext" not in source
