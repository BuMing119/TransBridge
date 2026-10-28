"""Repeated checks of real frozen requests do not revisit the task input set."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.ui.tools.ai_runtime_support import ai_task_runtime, frozen_ai_request
from transbridge.ui.tools.ai_translator.run_controller import RunController
from transbridge.ui.tools.ai_translator.run_spec import FrozenExecutionConfig
from transbridge.ui.tools.ai_translator.task_consistency import TaskConsistency


def test_frozen_request_checks_do_not_copy_config_or_iterate_large_scope(monkeypatch):
    visits = []

    class CountedKeys(tuple):
        def __iter__(self):
            visits.append(len(self))
            return super().__iter__()

    controller = RunController(task_runtime=ai_task_runtime())
    request = controller.begin("translate", SimpleNamespace(max_concurrent=1, model="test"), [])
    request = replace(request, spec=replace(request.spec, entry_keys=CountedKeys(str(i) for i in range(100_000))))
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    copies = []
    copy = FrozenExecutionConfig.copy

    def tracked_copy(value):
        copies.append(value)
        return copy(value)

    monkeypatch.setattr(FrozenExecutionConfig, "copy", tracked_copy)
    visits.clear()
    try:
        for _ in range(5):
            guard.require_current(request)
        assert visits == []
        assert copies == []
    finally:
        controller.finish(request.run_id)


@pytest.mark.parametrize("changed", ["spec", "config_snapshot", "terminology_binding"])
def test_fixed_request_envelope_changes_remain_guarded(changed):
    controller = RunController(task_runtime=ai_task_runtime())
    request = controller.begin("translate", SimpleNamespace(max_concurrent=1, model="test"), [])
    request = frozen_ai_request(request)
    guard = TaskConsistency(request)
    replacements = {
        "spec": replace(request.spec, config_digest="changed"),
        "config_snapshot": FrozenExecutionConfig(SimpleNamespace(model="other", max_concurrent=1)),
        "terminology_binding": SimpleNamespace(snapshot_ref="different-reference"),
    }
    try:
        with pytest.raises(RuntimeError, match="任务配置已变化"):
            guard.require_current(replace(request, **{changed: replacements[changed]}))
    finally:
        controller.finish(request.run_id)
