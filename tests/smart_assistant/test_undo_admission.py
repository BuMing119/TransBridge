"""Capture adapters never downgrade an assistant write to an independent write."""

from types import SimpleNamespace

import pytest

from transbridge.application.assistant_requests.models import RequestError
from transbridge.smart_assistant.tools.binding_undo_capture import capture_binding_command
from transbridge.smart_assistant.tools.config_undo_capture import save_config
from transbridge.smart_assistant.tools.file_undo_capture import capture_file_command
from transbridge.smart_assistant.tools.undo_capture import capture_variant_command


def _write(adapter, context, path, mutation):
    if adapter == "variant":
        return capture_variant_command(context, mutation)
    if adapter == "binding":
        return capture_binding_command(context, mutation)
    if adapter == "file":
        return capture_file_command(context, [path], mutation)
    return save_config(context, SimpleNamespace(save_to_file=mutation), ["temperature"])


@pytest.mark.parametrize("adapter", ["variant", "binding", "file", "config"])
@pytest.mark.parametrize("missing", ["service", "round", "gate"])
def test_incomplete_assistant_scope_never_calls_mutation_or_whole_config_save(tmp_path, adapter, missing):
    effect = SimpleNamespace(effect_id="effect", execution=SimpleNamespace(work_round_id=""))
    gate = SimpleNamespace(
        service=SimpleNamespace(undo=None if missing == "service" else object()),
        current_request=lambda: SimpleNamespace(effects=(effect,)),
    )
    context = SimpleNamespace(
        assistant_gate=None if missing == "gate" else gate,
        assistant_required=True,
        assistant_effect_id="effect",
        authorized_roots=(str(tmp_path),),
    )
    errors = {"service": "UNDO_STORAGE_UNAVAILABLE", "round": "UNDO_ROUND_SOURCE_MISSING", "gate": "TURN_LEASE_STALE"}
    path = tmp_path / "unchanged.txt"
    path.write_text("original")
    with pytest.raises(RequestError, match=errors[missing]):
        _write(adapter, context, path, lambda: path.write_text("changed"))
    assert path.read_text() == "original"


@pytest.mark.parametrize("adapter", ["variant", "binding", "file", "config"])
def test_independent_tool_call_still_executes_without_assistant_identity(tmp_path, adapter):
    context = SimpleNamespace(authorized_roots=(str(tmp_path),))
    path = tmp_path / "independent.txt"
    _write(adapter, context, path, lambda: path.write_text("independent"))
    assert path.read_text() == "independent"
