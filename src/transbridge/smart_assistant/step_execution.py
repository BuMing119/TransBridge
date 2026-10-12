"""Adapt logical request steps to guarded tool calls and the shared retry loop."""

from copy import copy

from transbridge.application.assistant_requests.models import RequestError
from transbridge.application.security import diagnostic_exception_text

from .reflexion.retry_executor import ToolRetryExecutor
from .tools.types import ToolResult


def run_step(
    step, context, invoke, *, executor=None, retry_allowed=False, tool_schema=None, cancelled=None, on_retry=None
):
    context = copy(context)
    context.assistant_step_managed = True
    gate = getattr(context, "assistant_gate", None)
    handle = None
    if gate is not None:
        try:
            handle = gate.begin_step(step["tool"], step.get("args", {}), retry_allowed=retry_allowed)
        except RequestError as error:
            return _admission_failure(gate, error)
        except Exception as error:
            failure = RequestError("ADMISSION_PERSIST_FAILED", diagnostic_exception_text(error))
            return _admission_failure(gate, failure)

    def attempt(args):
        attempt_number = None
        if handle is not None:
            try:
                attempt_number = gate.attempt(handle, args, context)
            except RequestError as error:
                return ToolResult.fail(str(error), error_category="permission", error_code=error.code)
        try:
            raw = invoke(args, context)
        except Exception as error:
            failure = ToolResult.fail(
                diagnostic_exception_text(error), error_category="internal", error_code=type(error).__name__
            )
            if handle is not None:
                gate.attempt_finished(handle, attempt_number, failure)
            raise
        result = ToolRetryExecutor._normalize_result(raw)
        if handle is not None:
            try:
                gate.attempt_finished(handle, attempt_number, result)
            except Exception as error:
                # The logical receipt below remains pending until persistence recovers.
                return ToolResult.fail(
                    diagnostic_exception_text(error),
                    error_category="permission",
                    error_code="ADMISSION_PERSIST_FAILED",
                )
        return raw

    try:
        if executor is None:
            result = attempt(step.get("args", {}))
        else:
            result = executor.execute(
                step,
                attempt,
                retry_allowed=retry_allowed,
                tool_schema=tool_schema,
                cancelled=cancelled,
                on_retry=on_retry,
            ).result
    except Exception as error:
        if handle is not None:
            gate.finish_step(handle, ToolResult.fail(diagnostic_exception_text(error), error_code=type(error).__name__))
        raise
    if handle is not None:
        try:
            gate.finish_step(handle, ToolRetryExecutor._normalize_result(result))
        except RequestError as error:
            return ToolResult.fail(str(error), error_category="permission", error_code=error.code)
    return result


def _admission_failure(gate, error):
    """No remaining step may silently retain ownership after the batch loses admission."""
    try:
        gate.interrupt_steps()
    except RequestError as persistence_error:
        error = persistence_error  # The writer has retained every unstarted receipt for retry.
    return ToolResult.fail(str(error), error_category="permission", error_code=error.code)


def reject_step(step, context, result):
    return run_step(step, context, lambda _args, _context: result)


def execute_retrying_step(spec, step, context, middlewares, executor, *, cancelled=None, on_retry=None):
    from .tools.base import execute_with_guardrails

    retry_allowed = (
        getattr(spec, "permission", "read") == "read"
        and not getattr(spec, "require_confirmation", False)
        and not getattr(spec, "is_long_running", False)
        and step.get("retry", True) is not False
    )
    return run_step(
        step,
        context,
        lambda args, execution_context: execute_with_guardrails(spec, args, execution_context, middlewares=middlewares),
        executor=executor,
        retry_allowed=retry_allowed,
        tool_schema=getattr(spec, "parameters", None),
        cancelled=cancelled,
        on_retry=on_retry,
    )
